"""Odamni tashqi ko'rinishi (kiyim, gavda) bo'yicha tanish: OSNet x1_0 (MSMT17).

Arxitektura torchreid'dagi OSNet bilan bir xil (og'irliklar shu kalitlarga to'g'ri
tushadi). Natija — 512 o'lchamli, uzunligi 1 ga keltirilgan vektor; ikki odam
o'xshashligi = vektorlarning skalyar ko'paytmasi (1 = bir xil, 0 = begona).
"""
from __future__ import annotations

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


class ConvLayer(nn.Module):
    def __init__(self, cin, cout, k, stride=1, padding=0):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, k, stride=stride, padding=padding, bias=False)
        self.bn = nn.BatchNorm2d(cout)

    def forward(self, x):
        return F.relu(self.bn(self.conv(x)))


class Conv1x1(nn.Module):
    def __init__(self, cin, cout, stride=1):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, 1, stride=stride, bias=False)
        self.bn = nn.BatchNorm2d(cout)

    def forward(self, x):
        return F.relu(self.bn(self.conv(x)))


class Conv1x1Linear(nn.Module):
    def __init__(self, cin, cout, stride=1):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, 1, stride=stride, bias=False)
        self.bn = nn.BatchNorm2d(cout)

    def forward(self, x):
        return self.bn(self.conv(x))


class LightConv3x3(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.conv1 = nn.Conv2d(cin, cout, 1, bias=False)
        self.conv2 = nn.Conv2d(cout, cout, 3, padding=1, bias=False, groups=cout)
        self.bn = nn.BatchNorm2d(cout)

    def forward(self, x):
        return F.relu(self.bn(self.conv2(self.conv1(x))))


class ChannelGate(nn.Module):
    def __init__(self, c, reduction=16):
        super().__init__()
        self.fc1 = nn.Conv2d(c, c // reduction, 1, bias=True)
        self.fc2 = nn.Conv2d(c // reduction, c, 1, bias=True)

    def forward(self, x):
        g = F.adaptive_avg_pool2d(x, 1)
        g = torch.sigmoid(self.fc2(F.relu(self.fc1(g))))
        return x * g


class OSBlock(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        mid = cout // 4
        self.conv1 = Conv1x1(cin, mid)
        self.conv2a = LightConv3x3(mid, mid)
        self.conv2b = nn.Sequential(*[LightConv3x3(mid, mid) for _ in range(2)])
        self.conv2c = nn.Sequential(*[LightConv3x3(mid, mid) for _ in range(3)])
        self.conv2d = nn.Sequential(*[LightConv3x3(mid, mid) for _ in range(4)])
        self.gate = ChannelGate(mid)
        self.conv3 = Conv1x1Linear(mid, cout)
        self.downsample = Conv1x1Linear(cin, cout) if cin != cout else None

    def forward(self, x):
        x1 = self.conv1(x)
        x2 = (self.gate(self.conv2a(x1)) + self.gate(self.conv2b(x1))
              + self.gate(self.conv2c(x1)) + self.gate(self.conv2d(x1)))
        x3 = self.conv3(x2)
        identity = self.downsample(x) if self.downsample is not None else x
        return F.relu(x3 + identity)


def _make_layer(n, cin, cout, reduce):
    layers = [OSBlock(cin, cout)] + [OSBlock(cout, cout) for _ in range(n - 1)]
    if reduce:
        layers.append(nn.Sequential(Conv1x1(cout, cout), nn.AvgPool2d(2, stride=2)))
    return nn.Sequential(*layers)


class OSNet(nn.Module):
    def __init__(self, num_classes=4101):
        super().__init__()
        self.conv1 = ConvLayer(3, 64, 7, stride=2, padding=3)
        self.maxpool = nn.MaxPool2d(3, stride=2, padding=1)
        self.conv2 = _make_layer(2, 64, 256, True)
        self.conv3 = _make_layer(2, 256, 384, True)
        self.conv4 = _make_layer(2, 384, 512, False)
        self.conv5 = Conv1x1(512, 512)
        self.fc = nn.Sequential(nn.Linear(512, 512), nn.BatchNorm1d(512), nn.ReLU(inplace=True))
        self.classifier = nn.Linear(512, num_classes)

    def forward(self, x):
        x = self.maxpool(self.conv1(x))
        x = self.conv5(self.conv4(self.conv3(self.conv2(x))))
        x = F.adaptive_avg_pool2d(x, 1).flatten(1)
        return self.fc(x)


_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class ReID:
    """Odam kesmalaridan (BGR) ko'rinish vektorlarini chiqaradi."""

    def __init__(self, weights: str, device: str = "cuda"):
        self.device = device if (device != "cuda" or torch.cuda.is_available()) else "cpu"
        sd = torch.load(weights, map_location="cpu", weights_only=True)
        sd = sd.get("state_dict", sd)
        sd = {k.removeprefix("module."): v for k, v in sd.items()}
        net = OSNet(num_classes=sd["classifier.weight"].shape[0])
        net.load_state_dict(sd, strict=True)
        self.net = net.eval().to(self.device)
        if self.device == "cuda":
            self.net = self.net.half()

    @torch.inference_mode()
    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        """crops: BGR kesmalar ro'yxati -> (N, 512) normallashgan vektorlar."""
        if not crops:
            return np.zeros((0, 512), dtype=np.float32)
        import cv2
        batch = []
        for c in crops:
            img = cv2.resize(c, (128, 256), interpolation=cv2.INTER_LINEAR)
            img = img[:, :, ::-1].astype(np.float32) / 255.0
            batch.append((img - _MEAN) / _STD)
        x = torch.from_numpy(np.stack(batch).transpose(0, 3, 1, 2)).to(self.device)
        if self.device == "cuda":
            x = x.half()
        # gorizontal aks bilan o'rtacha — barqarorroq
        f = self.net(x) + self.net(torch.flip(x, dims=[3]))
        f = F.normalize(f.float(), dim=1)
        return f.cpu().numpy()
