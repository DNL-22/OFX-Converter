#!/usr/bin/env bash
# ────────────────────────────────────────────
#  PDF → OFX Converter — startup script
#  Banco do Brasil · Conversão em lote
# ────────────────────────────────────────────

echo ""
echo "  PDF → OFX Converter"
echo "  ───────────────────"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "  ✗  Python 3 não encontrado. Instale em https://python.org"
  exit 1
fi

# Install dependencies if needed
python3 -c "import fitz, flask" 2>/dev/null || {
  echo "  ⟳  Instalando dependências (pymupdf, flask)…"
  pip3 install pymupdf flask --quiet
}

echo "  ✓  Dependências OK"
echo ""
echo "  ▶  Abrindo em http://localhost:5050"
echo "  ✕  Ctrl+C para encerrar"
echo ""

cd "$(dirname "$0")"
python3 app.py
