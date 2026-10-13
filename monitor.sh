#!/bin/bash
# odoo_telegram -- terminalda uzluksiz monitoring.
# Ishga tushirish: bash monitor.sh   (to'xtatish: Ctrl+C)

cd "$(dirname "$0")" || exit 1

while true; do
    clear
    echo "======================================================"
    echo " odoo_telegram monitoring -- $(date '+%Y-%m-%d %H:%M:%S')"
    echo "======================================================"

    echo
    echo "--- Health (FastAPI) ---"
    HEALTH=$(curl -s -m 5 http://localhost:8000/health)
    if [ -n "$HEALTH" ]; then
        echo "✅ $HEALTH"
    else
        echo "❌ JAVOB YO'Q (fastapi konteyner o'chgan yoki osilib qolgan bo'lishi mumkin)"
    fi

    echo
    echo "--- Konteynerlar holati ---"
    docker compose ps

    echo
    echo "--- So'nggi 5 daqiqadagi xatoliklar (barcha servislar) ---"
    ERRORS=$(docker compose logs --since 5m 2>/dev/null | grep -iE "error|exception|traceback|critical")
    if [ -n "$ERRORS" ]; then
        echo "$ERRORS" | tail -20
    else
        echo "Xatolik topilmadi."
    fi

    echo
    echo "(Har 10 soniyada avtomatik yangilanadi -- to'xtatish uchun Ctrl+C)"
    sleep 10
done
