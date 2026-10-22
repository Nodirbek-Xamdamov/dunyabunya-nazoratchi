"""Rule-based "day summary": template sentences filled with exact numbers (no AI numbers)."""

from __future__ import annotations

import pandas as pd

from core import metrics as m_
from core.timeutil import fmt_duration, fmt_int, fmt_pct
from ui.i18n import t


def summary(kpis: m_.Kpis, previous: m_.Kpis | None, categories: pd.DataFrame | None,
            top_group: tuple[str, int] | None) -> list[str]:
    if kpis.related == 0:  # messages not about the company are not counted anywhere
        return [t("nar.empty")]
    sentences = [t("nar.volume", related=fmt_int(kpis.related))]
    if kpis.complaints:
        text = t("nar.complaints", n=fmt_int(kpis.complaints), rate=fmt_pct(kpis.complaint_rate))
        if previous is not None:
            if previous.complaints < m_.SMALL_BASE:
                text += " " + t("nar.complaints.vs_abs", prev=fmt_int(previous.complaints))
            else:
                text += " " + t("nar.complaints.vs_pct", change=m_.change(kpis.complaints, previous.complaints).text)
        sentences.append(text)
    else:
        sentences.append(t("nar.complaints.none"))
    if categories is not None and not categories.empty and categories["related"].sum() > 0:
        top = categories.sort_values(["related", "complaints"], ascending=False).iloc[0]
        sentences.append(t("nar.top_category", category=top["category"], n=fmt_int(top["related"]),
                           complaints=fmt_int(top["complaints"])))
    if kpis.answered + kpis.missed:
        sentences.append(t("nar.sla", requests=fmt_int(kpis.answered + kpis.missed), answered=fmt_int(kpis.answered),
                           sla=fmt_pct(kpis.sla), missed=fmt_int(kpis.missed)))
    else:
        sentences.append(t("nar.sla.none"))
    if kpis.median_response_s is not None:
        sentences.append(t("nar.median", median=fmt_duration(kpis.median_response_s)))
    if top_group and top_group[1]:
        sentences.append(t("nar.top_group", group=top_group[0], n=fmt_int(top_group[1])))
    return sentences[:6]
