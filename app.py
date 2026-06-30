#!/usr/bin/env python3
"""PDF -> OFX Converter — Brazilian Bank Statements"""

import os, re, io, zipfile, tempfile
from datetime import datetime
from flask import Flask, request, jsonify, send_file, render_template

try:
    import fitz
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

APP_VERSION = "2.0.0"

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

BANKS = {
    "bb": {
        "label":  "Banco do Brasil",
        "bankid": "001",
        "fid":    "001",
        "org":    "Banco do Brasil S.A.",
    },
    "mp": {
        "label":  "Mercado Pago",
        "bankid": "323",
        "fid":    "323",
        "org":    "Mercado Pago",
    },
    "bradesco": {
        "label":  "Bradesco",
        "bankid": "0237",
        "fid":    "0237",
        "org":    "Banco Bradesco S.A.",
    },
    "btg": {
        "label":  "BTG Pactual",
        "bankid": "208",
        "fid":    "208",
        "org":    "BTG Pactual",
    },
    "santander": {
        "label":  "Santander",
        "bankid": "033",
        "fid":    "033",
        "org":    "Banco Santander (Brasil) S.A.",
    },
    "itau": {
        "label":  "Itau",
        "bankid": "341",
        "fid":    "341",
        "org":    "Itau Unibanco S.A.",
    },
}

# ─────────────────────────────────────────────
#  Shared regexes
# ─────────────────────────────────────────────
DATE_RE    = re.compile(r'^\d{2}/\d{2}/\d{4}$')
SALDO_RE   = re.compile(r'saldo (anterior|do dia)', re.IGNORECASE)
STOP_RE    = re.compile(r'^s\s+a\s+l\s+d\s+o$', re.IGNORECASE)

def parse_date(s):
    try:
        return datetime.strptime(s.strip(), '%d/%m/%Y').strftime('%Y%m%d')
    except ValueError:
        return None

def parse_amount(num_str, sign_char):
    """sign_char: '+'/'-' (format 1) or 'C'/'D' (format 2)"""
    try:
        v = float(num_str.replace('.', '').replace(',', '.'))
        debit = sign_char in ('-', 'D')
        return (-v, 'DEBIT') if debit else (v, 'CREDIT')
    except ValueError:
        return None, None


# ═══════════════════════════════════════════════
#  FORMAT 1 — "Extrato de Conta Corrente"
#  (det2.pdf style — value-anchored stream)
# ═══════════════════════════════════════════════

# Value line: "1.544,69 (+)"
F1_VALUE_RE = re.compile(r'^([\d.]+,\d{2})\s*\(([+\-])\)$')
F1_NUMERIC  = re.compile(r'^\d+$')           # lote / doc — pure digits, skip

F1_SKIP_EXACT = {
    'dia', 'lote', 'documento', 'histórico', 'historico',
    'valor', 'lançamentos', 'lancamentos', 'cliente',
}
F1_SKIP_CONTAINS = [
    'saldo do dia', 'saldo anterior',
    'extrato de conta', 'agência:', 'conta:',
    'informações adicionais', 'informações complementares',
    'total aplicações', 'sujeitos a confirmação',
    'limite ouro', 'taxa cheque', 'tributos (iof)',
    'custo efetivo', 'data venc.', 'valor total devido',
    'valor liberado', 'despesas-(iof)', 'simulação para',
    '* saldos por dia',
]
F1_STOP_STARTS = (
    'informações adicionais', 'informações complementares',
    'limite ouro', 'taxa cheque especial', 'tributos (iof)',
    'custo efetivo', 'simulação para', 'total aplicações',
    '* saldos por dia',
)

def _f1_skip(line):
    lo = line.lower().strip()
    if not lo or lo in F1_SKIP_EXACT: return True
    return any(kw in lo for kw in F1_SKIP_CONTAINS)

def _f1_stop(line):
    lo = line.lower().strip()
    if STOP_RE.match(lo): return True
    return any(lo.startswith(kw) for kw in F1_STOP_STARTS)

BB1_HEADER_RE = re.compile(
    r'^Extrato de Conta Corrente\nCliente .+?\nAg[eê]ncia:.+?Conta:.+?\nLançamentos\n'
    r'Dia\nLote\nDocumento\nHistórico\nValor\n',
    re.MULTILINE)

def _parse_bb1(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()

    stripped = [pages[0]] + [BB1_HEADER_RE.sub('', p, count=1) for p in pages[1:]]
    full = '\n'.join(stripped)

    # Fix truncated dates at page breaks (DD/MM/YYY → DD/MM/YYYY)
    from collections import Counter
    full_years = re.findall(r'\d{2}/\d{2}/(\d{4})', full)
    if full_years:
        last = Counter(full_years).most_common(1)[0][0][-1]
        full = re.sub(r'^(\d{2}/\d{2}/\d{3})$',
                      lambda m: m.group(1) + last, full, flags=re.MULTILINE)

    # Agency / account
    agency, account = '', ''
    m = re.search(r'Ag[eê]ncia:\s*([0-9\-]+)\s+Conta:\s*([0-9\-]+)', pages[0], re.I)
    if m:
        agency  = re.sub(r'\D', '', m.group(1))
        account = re.sub(r'\D', '', m.group(2))

    lines = [l.strip() for l in full.splitlines()]
    transactions = []
    pending = None

    def flush():
        nonlocal pending
        if pending and pending['amount'] is not None and pending['date'] is not None:
            desc = ' '.join(pending['desc_parts']).strip()
            if not SALDO_RE.search(desc):
                transactions.append({'date': pending['date'], 'desc': desc,
                                     'amount': pending['amount'], 'trntype': pending['trntype']})
        pending = None

    for line in lines:
        if not line: continue
        if _f1_stop(line): flush(); break

        vm = F1_VALUE_RE.match(line)
        if vm:
            flush()
            amount, trntype = parse_amount(vm.group(1), vm.group(2))
            pending = {'date': None, 'desc_parts': [], 'amount': amount, 'trntype': trntype}
            continue

        if pending is None: continue

        if DATE_RE.match(line) and pending['date'] is None:
            pending['date'] = parse_date(line)
            continue
        if _f1_skip(line): continue
        if F1_NUMERIC.match(line): continue
        pending['desc_parts'].append(line)

    flush()
    return {'agency': agency, 'account': account, 'transactions': transactions}


# ═══════════════════════════════════════════════
#  FORMAT 2 — "Consultas - Extrato de conta corrente"
#  (06_2024.pdf style — date-anchored stream)
# ═══════════════════════════════════════════════

# Ag. origem: exactly 4 digits
F2_AG_RE       = re.compile(r'^\d{4}$')
# Lote (5 digits) + doc-seq (3 digits) + description text
F2_LOTE_RE     = re.compile(r'^\d{5}\s+\d{3}\s+(.+)$')
# A number with only digits and dots (no comma) = document/checknum
F2_DOC_RE      = re.compile(r'^[\d.]+$')
# Value pattern: Brazilian decimal number + D/C sign
F2_VALUE_RE    = re.compile(r'([\d.]+,\d{2})\s+([DC])')

F2_STOP_STARTS = (
    'observações', 'seguro empresarial', 'transação efetuada',
    'serviço de atendimento', 'ouvidoria', 'para deficientes',
    '------------------------------------------------',
    'informações complementares', 'limite ouro',
    'taxa ch.', 'custo efetivo', 'data vencimento',
    'valor total devido', 'valor liberado', 'despesas vinculadas',
    'simulação para', '(*)', '- tributos', '- tarifa',
)

def _f2_stop(line):
    lo = line.lower().strip()
    if STOP_RE.match(lo): return True
    if lo.startswith('*** a conta'): return False   # keep; it's just a note
    return any(lo.startswith(kw) for kw in F2_STOP_STARTS)

def _f2_skip_continuation(line):
    """Lines that should never be appended to desc in format 2."""
    lo = line.lower().strip()
    # Percentage lines, pure-number lines, CET table values
    if re.match(r'^[\d.,]+%?$', lo): return True
    if re.match(r'^\d{2}/\d{2}/\d{4}\s*$', lo): return True   # stray date
    return False

def _parse_bb2(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()

    # No page header to strip — just concatenate raw
    full = '\n'.join(pages)

    # Agency / account
    agency, account = '', ''
    m = re.search(r'Ag[eê]ncia\s*\n\s*([0-9\-]+)', full, re.I)
    if m: agency = re.sub(r'\D', '', m.group(1))
    m = re.search(r'Conta corrente\s*\n\s*([0-9A-Za-z\-]+)', full, re.I)
    if m:
        raw = m.group(1).strip()
        # e.g. "14027-9MODERNIZA..." → keep only the account number part
        account = re.match(r'[\d\-]+', raw).group(0) if re.match(r'[\d\-]', raw) else raw

    lines = [l.strip() for l in full.splitlines()]
    transactions = []
    pending = None       # {date, desc_parts, checknum, amount, trntype}

    def flush():
        nonlocal pending
        if pending and pending['amount'] is not None and pending['date']:
            desc = ' - '.join(p for p in pending['desc_parts'] if p).strip(' -')
            if not SALDO_RE.search(desc):
                transactions.append({
                    'date':    pending['date'],
                    'desc':    desc,
                    'amount':  pending['amount'],
                    'trntype': pending['trntype'],
                })
        pending = None

    for line in lines:
        if not line: continue
        if _f2_stop(line): flush(); break

        # ── Date line ──────────────────────────────────────────────
        if DATE_RE.match(line):
            d = parse_date(line)
            if (pending is not None
                    and not pending['desc_parts']
                    and pending['amount'] is None
                    and pending['checknum'] is None):
                # Second consecutive date = Dt. movimento → use as DTPOSTED
                pending['date'] = d
            else:
                flush()
                pending = {'date': d, 'desc_parts': [], 'checknum': None,
                           'amount': None, 'trntype': None}
            continue

        if pending is None: continue

        # ── Ag. origem (4-digit) ────────────────────────────────────
        if F2_AG_RE.match(line): continue

        # ── Lote + doc-seq + description ───────────────────────────
        m = F2_LOTE_RE.match(line)
        if m:
            desc_token = m.group(1)
            if STOP_RE.match(desc_token.lower().strip()):  # e.g. "S A L D O"
                flush(); break
            pending['desc_parts'].append(desc_token)
            continue

        # ── Value (possibly preceded by doc number on same line) ───
        vm = F2_VALUE_RE.search(line)
        if vm:
            if pending['amount'] is None:   # take only the first value per entry
                before = line[:vm.start()].strip()
                if before and F2_DOC_RE.match(before) and pending['checknum'] is None:
                    pending['checknum'] = before.replace('.', '')
                pending['amount'], pending['trntype'] = parse_amount(vm.group(1), vm.group(2))
            # anything after the value on the same line is balance — ignore
            continue

        # ── Pure document number (no comma) ────────────────────────
        if F2_DOC_RE.match(line):
            if pending['checknum'] is None:
                pending['checknum'] = line.replace('.', '')
            continue

        # ── Description continuation ────────────────────────────────
        if not _f2_skip_continuation(line):
            pending['desc_parts'].append(line)

    flush()
    return {'agency': agency, 'account': account, 'transactions': transactions}


# ═══════════════════════════════════════════════
#  FORMAT MP — "EXTRATO DE CONTA" (Mercado Pago)
#  Date-anchored; ID separates desc from value
# ═══════════════════════════════════════════════

MP_DATE_RE  = re.compile(r'^\d{2}-\d{2}-\d{4}$')
MP_ID_RE    = re.compile(r'^\d{8,}$')          # operation ID — pure digits ≥8
MP_VALUE_RE = re.compile(r'^R\$\s*([-\d.,]+)$')

def _mp_parse_date(s):
    try:
        return datetime.strptime(s.strip(), '%d-%m-%Y').strftime('%Y%m%d')
    except ValueError:
        return None

def _mp_parse_amount(s):
    try:
        v = float(s.replace('.', '').replace(',', '.'))
        return (v, 'DEBIT') if v < 0 else (v, 'CREDIT')
    except ValueError:
        return None, None

def _parse_mp(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()
    full = '\n'.join(pages)

    # ── Agency / account ──────────────────────────────────────────
    # In the MP text stream the values render BEFORE their labels (column layout)
    # " 1\n 56856405982\nAgência:\nConta:"
    agency, account = '1', ''
    m = re.search(r'\n\s*(\d+)\n\s*(\d{6,})\nAg[eê]ncia:\nConta:', full)
    if m:
        agency  = m.group(1).strip()
        account = m.group(2).strip()

    # ── Period dates ──────────────────────────────────────────────
    dt_start, dt_end = '', ''
    m = re.search(r'De\s+(\d{2}-\d{2}-\d{4})\s+al\s+(\d{2}-\d{2}-\d{4})', full)
    if m:
        dt_start = _mp_parse_date(m.group(1)) or ''
        dt_end   = _mp_parse_date(m.group(2)) or ''

    # ── Find transaction block ────────────────────────────────────
    marker = 'DETALHE DOS MOVIMENTOS'
    idx = full.find(marker)
    if idx == -1:
        return {'agency': agency, 'account': account, 'transactions': []}
    body = full[idx + len(marker):]

    lines = [l.strip() for l in body.splitlines()]
    transactions = []
    pending = None   # {date, desc_parts, op_id, amount, trntype}

    def flush():
        nonlocal pending
        if pending and pending['amount'] is not None:
            transactions.append({
                'date':    pending['date'],
                'desc':    ' '.join(pending['desc_parts']).strip(),
                'amount':  pending['amount'],
                'trntype': pending['trntype'],
                'op_id':   pending['op_id'] or '',
            })
        pending = None

    for line in lines:
        if not line: continue

        # Stop at footer lines
        lo = line.lower()
        if lo.startswith('saldo final') or lo.startswith('data de geração'):
            flush(); break

        # ── Date → new entry ──
        if MP_DATE_RE.match(line):
            flush()
            pending = {'date': _mp_parse_date(line), 'desc_parts': [],
                       'op_id': None, 'amount': None, 'trntype': None}
            continue

        if pending is None: continue

        # ── Operation ID ──
        if MP_ID_RE.match(line) and pending['op_id'] is None:
            pending['op_id'] = line
            continue

        # ── Value line ──
        vm = MP_VALUE_RE.match(line)
        if vm:
            if pending['amount'] is None:   # first = transaction value
                pending['amount'], pending['trntype'] = _mp_parse_amount(vm.group(1))
            # second occurrence = running balance → skip
            continue

        # ── Skip header column labels ──
        if line in ('Data', 'Descrição', 'ID da operação', 'Valor', 'Saldo'):
            continue

        # ── Description line ──
        if pending['op_id'] is None:   # only append desc before we've seen the ID
            pending['desc_parts'].append(line)

    flush()
    return {'agency': agency, 'account': account,
            'dt_start': dt_start, 'dt_end': dt_end,
            'transactions': transactions}


# ═══════════════════════════════════════════════
#  FORMAT BRADESCO — "Extrato Mensal / Por Período"
#  Date-anchored; entries separated by DOCNUM→VALUE→BALANCE triplet
# ═══════════════════════════════════════════════

BRAD_DATE_RE   = re.compile(r'^\d{2}/\d{2}/\d{4}$')
BRAD_DOCNUM_RE = re.compile(r'^\d+$')                          # pure digits, no comma
BRAD_VALUE_RE  = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$') # Brazilian decimal

# Lines that are structural noise — skip silently
BRAD_SKIP_EXACT = {
    'Data', 'Lançamento', 'Dcto.', 'Crédito (R$)', 'Débito (R$)', 'Saldo (R$)',
    'Agência | Conta', 'Total Disponível (R$)', 'Total (R$)',
    'Histórico', 'Valor (R$)', 'Últimos Lançamentos',
    'e',   # stray word from garbled header in some versions
}
BRAD_SKIP_STARTS = (
    'extrato mensal', 'extrato de:', 'inovação contabil', 'inovacao contabil',
    'nome do usuário', 'data da operação', 'agência |', 'os dados acima',
    'banco bradesco', 'folha ', 'http', 'of 2', '1 of', '2 of',
    'cnpj:', 'total disponível', 'não há lançamentos',
    '(smc.wse', 'entre ', 'ag: ', '| cc:',
)
BRAD_STOP_STARTS = (
    'saldos invest fácil', 'saldo invest fácil',
)

def _brad_is_noise(line):
    lo = line.lower().strip()
    if not lo: return True
    if lo in {s.lower() for s in BRAD_SKIP_EXACT}: return True
    if any(lo.startswith(s) for s in BRAD_SKIP_STARTS): return True
    # Page-number patterns: "1 of 2", "2 of 2", "05/03/2021 11:43"
    if re.match(r'^\d+ of \d+$', lo): return True
    if re.match(r'^\d{2}/\d{2}/\d{4} \d{2}h\d{2}$', lo): return True
    # Agência/Conta data line like "01580 | 0002881-9"
    if re.match(r'^\d{5}\s*\|\s*\d{7}-\d$', lo): return True
    return False

def _brad_is_stop(line):
    lo = line.lower().strip()
    return any(lo.startswith(s) for s in BRAD_STOP_STARTS)

def _brad_parse_amount(s):
    s = s.strip()
    neg = s.startswith('-')
    num = float(s.lstrip('-').replace('.', '').replace(',', '.'))
    v = -num if neg else num
    return (v, 'DEBIT') if neg else (v, 'CREDIT')

def _parse_bradesco(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()

    # Agency / account from first page
    agency, account = '', ''
    m = re.search(r'(\d{5})\s*\|\s*(\d{7}-\d)', pages[0])
    if m:
        agency  = m.group(1).lstrip('0') or '0'
        account = m.group(2)

    # Period dates
    dt_start, dt_end = '', ''
    period_text = '\n'.join(pages)
    # Two strategies: normal "Entre D1 e D2" or garbled "D1  D2 ... Entre"
    m = re.search(r'Entre\s+(\d{2}/\d{2}/\d{4})\s+e?\s+(\d{2}/\d{2}/\d{4})', period_text, re.I)
    if not m:
        # Garbled: dates appear before the word "Entre" on same text block
        m = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})', period_text)
    if m:
        dt_start = parse_date(m.group(1)) or ''
        dt_end   = parse_date(m.group(2)) or ''

    # Concatenate pages, then work line-by-line
    full = '\n'.join(pages)
    lines = [l.strip() for l in full.splitlines()]

    transactions = []

    # State
    current_date = None
    desc_parts   = []
    doc_num      = None
    in_total     = False   # True while skipping "Total ..." block
    stopped      = False

    def flush(amount_str):
        nonlocal desc_parts, doc_num
        if not current_date or not desc_parts:
            desc_parts, doc_num = [], None
            return
        desc = ' '.join(desc_parts).strip()
        # Skip balance markers
        if re.search(r'saldo (anterior|invest)', desc, re.I):
            desc_parts, doc_num = [], None
            return
        amount, trntype = _brad_parse_amount(amount_str)
        transactions.append({
            'date':    current_date,
            'desc':    desc,
            'amount':  amount,
            'trntype': trntype,
        })
        desc_parts, doc_num = [], None

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        if not line.strip():
            continue

        if _brad_is_stop(line):
            stopped = True
            break

        if _brad_is_noise(line):
            continue

        # Skip "Total" block: "Total\nNUMBER\nNUMBER\nNUMBER"
        if line.lower() == 'total':
            in_total = True
            continue
        if in_total:
            if BRAD_VALUE_RE.match(line):
                continue   # skip totals numbers
            else:
                in_total = False  # non-number ends the total block
                # fall through to process this line normally

        # New date → start fresh (clears any stale SALDO ANTERIOR desc_parts)
        if BRAD_DATE_RE.match(line):
            d = parse_date(line)
            if d:
                current_date = d
                desc_parts, doc_num = [], None
            continue

        if current_date is None:
            continue   # haven't reached the transaction table yet

        # Doc number (pure digits, no comma) → separates desc from value
        if BRAD_DOCNUM_RE.match(line) and doc_num is None and desc_parts:
            doc_num = line
            continue

        # Value line (has comma)
        if BRAD_VALUE_RE.match(line):
            if doc_num is not None:
                flush(line)
                # Next line is the balance → skip it
                if i < len(lines) and BRAD_VALUE_RE.match(lines[i].strip()):
                    i += 1
            else:
                # Value without docnum = SALDO ANTERIOR balance line → clear stale state
                desc_parts, doc_num = [], None
            continue

        # Description line
        if doc_num is None:  # only accumulate desc before doc_num is seen
            desc_parts.append(line)

    return {'agency': agency, 'account': account,
            'dt_start': dt_start, 'dt_end': dt_end,
            'transactions': transactions}


# ═══════════════════════════════════════════════
#  FORMAT BTG — "Conta corrente - PJ" (BTG Pactual)
#  Date-anchored; value → balance per entry; split by month
# ═══════════════════════════════════════════════

BTG_DATE_RE  = re.compile(r'^\d{2}/\d{2}/\d{4}$')
# Brazilian decimal, optionally negative, with thousand separators
BTG_VALUE_RE = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')

BTG_SKIP_LINES = {
    'Data lançamento', 'Descrição do lançamento',
    'Entradas / Saídas (R$)', 'Saldo (R$)',
    'Fale com nossa central de atendimento',
    'Atendimento 24 horas por dia, 7 dias por semana',
    'Ouvidoria: 0800-722-0048',
}
BTG_SKIP_STARTS = (
    'ligue para:', 'das 9h', '© 2026 btg', '© btg',
    'sac:', 'ouvidoria:',
    'r$ ', 'tef\n', 'pix\n', 'boleto\n', 'outros\n',  # summary table
    'total de entradas', 'total de saídas',
    '01. conta corrente', '02. lançamentos',
    'saldo de abertura em', 'saldo de fechamento em',
    'saldo bloqueado em', 'pdf gerado em',
    'razão social', 'cnpj', 'banco\n', 'agência\n', 'conta\n',
    'período do extrato',
)

def _btg_is_noise(line):
    lo = line.lower().strip()
    if not lo: return True
    if line.strip() in BTG_SKIP_LINES: return True
    if any(lo.startswith(s) for s in BTG_SKIP_STARTS): return True
    # Page numbers: "1 / 25", "25 / 25"
    if re.match(r'^\d+\s*/\s*\d+$', lo): return True
    # Percentage lines from summary: "73.88%", "58.50%"
    if re.match(r'^\d+\.\d+%$', lo): return True
    return False

def _btg_parse_amount(s):
    neg = s.startswith('-')
    v = float(s.lstrip('-').replace('.', '').replace(',', '.'))
    return (-v, 'DEBIT') if neg else (v, 'CREDIT')

def _parse_btg(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()

    # ── Metadata from page 0 ──────────────────────────────────────
    p0 = pages[0]
    agency, account = '', ''
    m = re.search(r'Ag[eê]ncia\s*\n(\d+)', p0)
    if m: agency = m.group(1).strip()
    m = re.search(r'Conta\s*\n(\d+)', p0)
    if m: account = m.group(1).strip()

    # ── Concatenate all pages ──────────────────────────────────────
    full = '\n'.join(pages)
    lines = [l.strip() for l in full.splitlines()]

    # ── Parse ──────────────────────────────────────────────────────
    transactions = []
    current_date = None
    desc_parts   = []
    got_value    = False   # True after first value seen (next value = balance)
    current_amt  = None
    current_type = None
    in_table     = False   # True once we've passed the column headers

    def flush():
        nonlocal current_date, desc_parts, got_value, current_amt, current_type
        if current_date and current_amt is not None:
            desc = ' '.join(desc_parts).strip()
            transactions.append({
                'date':    current_date,
                'desc':    desc,
                'amount':  current_amt,
                'trntype': current_type,
            })
        current_date, desc_parts = None, []
        got_value, current_amt, current_type = False, None, None

    for line in lines:
        if not line: continue

        lo = line.lower()

        # Activate table mode once we see the column header
        if not in_table:
            if lo == 'data lançamento':
                in_table = True
            continue   # skip everything before the table

        # Stop at closing balance line (only valid inside the table)
        if lo.startswith('saldo de fechamento'):
            flush()
            break

        if _btg_is_noise(line): continue

        # Date
        if BTG_DATE_RE.match(line):
            flush()
            current_date = parse_date(line)
            continue

        if current_date is None: continue

        # Value / balance
        if BTG_VALUE_RE.match(line) or line == '-':
            if not got_value:
                if line == '-':
                    # Saldo de abertura placeholder — abandon this entry
                    flush()
                else:
                    current_amt, current_type = _btg_parse_amount(line)
                    got_value = True
            else:
                # Second number = running balance → flush entry, ready for next
                flush()
            continue

        # Skip saldo rows by description
        if re.search(r'saldo de (abertura|fechamento)', line, re.I):
            flush()
            continue

        # Description line (only before value is seen)
        if not got_value:
            desc_parts.append(line)

    flush()

    # ── Group by YYYYMM ───────────────────────────────────────────
    from collections import defaultdict
    monthly = defaultdict(list)
    for t in transactions:
        ym = t['date'][:6]
        monthly[ym].append(t)

    return {
        'agency':  agency,
        'account': account,
        'monthly': dict(monthly),   # { 'YYYYMM': [txns...] }
    }


# ═══════════════════════════════════════════════
#  FORMAT SANTANDER — "EXTRATO CONSOLIDADO INTELIGENTE"
#  Doc-anchored: desc → doc → value sequence per entry
# ═══════════════════════════════════════════════

SANT_DATE_RE  = re.compile(r'^\d{2}/\d{2}\s*$')           # "01/08"
SANT_DOC_RE   = re.compile(r'^(\d+|-)$')                  # pure digits or dash
SANT_VALUE_RE = re.compile(r'^([\d.]+,\d{2})([-\s]?)\s*$')# "540,00-" or "200.000,00 "
SANT_STOP_RE  = re.compile(r'^SALDO EM \d{2}/\d{2}')

SANT_PT_MONTHS = {
    'janeiro':1,'fevereiro':2,'março':3,'marco':3,'abril':4,'maio':5,
    'junho':6,'julho':7,'agosto':8,'setembro':9,'outubro':10,
    'novembro':11,'dezembro':12
}

SANT_SKIP_LINES = {
    'Data','Descrição','Nº Documento','Movimentos (R$)','Saldo (R$)',
    'Créditos','Débitos','Conta Corrente','Movimentação',
}
SANT_SKIP_STARTS = (
    'extrato_pj_','balp_','pagina:','extrato consolidado inteligente',
    'agosto/','janeiro/','fevereiro/','março/','abril/','maio/','junho/',
    'julho/','setembro/','outubro/','novembro/','dezembro/',
    'fale conosco','loja:','central de atendimento','consultas, inform',
    'de segunda','4004 ','0800 ','ouvidoria','se não ficou','ou pelo nosso',
    'no exterior','sac\n','reclamações','todos os dias','55 11','libras (',
    'acesse:','central de vendas','para contrata','das 8h','rezado cliente',
    'soluções em pagamentos','o santander tem','nossos serviços',
    ' - getnet',' - cobranças',' - pix',' - pagamento a',' - tributos',' - fopa',
    'nome\n','agência\n','(=)','(+)','(-)',
    'saldo disponível','total de créditos','depósitos / transf','outros créditos',
    'total de débitos','pagamentos / transf','outros débitos',
    'provisão de encargos','limite de cheque',
    '*valores','saldos por período','débito automático',
    'créditos contratados','pacote de serviços','índices econômicos',
    'você e seu dinheiro','quer avançar','conheça o','cuidado com',
    'conta mais','valor da mensalidade','status do débito',
    'dia de débito','produto','se algum de seus',
    'cheque empresa ou','desde de ','para efeito',
    '¹ a pontuação','² o saldo médio',
    'pontos','25.000 a','50.000 a','75.000 a','acima de',
    'ibovespa','igpm','incc','inpc','ipca','cdi\n','tr\n','poupanca',
    'euro\n','dolar ',
)

def _sant_is_noise(line):
    lo = line.lower().strip()
    if not lo: return True
    if line.strip() in SANT_SKIP_LINES: return True
    return any(lo.startswith(s) for s in SANT_SKIP_STARTS)

def _sant_parse_amount(num_str, sign_char):
    v = float(num_str.replace('.','').replace(',','.'))
    return (-v, 'DEBIT') if sign_char == '-' else (v, 'CREDIT')

def _parse_santander(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()
    full = '\n'.join(pages)

    # ── Year from "Resumo - agosto/2025" ─────────────────────────
    year = ''
    m = re.search(r'Resumo\s*-\s*(\w+)/(\d{4})', full, re.I)
    if m: year = m.group(2)

    # ── Agency / account ──────────────────────────────────────────
    agency, account = '', ''
    m = re.search(r'Agência\s*\n\s*(\d+)', full)
    if m: agency = m.group(1).strip()
    m = re.search(r'Conta Corrente\s*\n\s*([\d.\-]+)', full)
    if m: account = re.sub(r'[.\-]','', m.group(1)).strip()

    # ── Parse line by line ────────────────────────────────────────
    lines = [l.rstrip() for l in full.splitlines()]
    transactions = []
    current_date = None   # YYYYMMDD
    desc_parts   = []
    doc_num      = None   # set when doc line seen
    in_table     = False  # True after opening balance line

    def flush(value_str, sign_char):
        nonlocal desc_parts, doc_num
        if not current_date or not desc_parts: 
            desc_parts, doc_num = [], None
            return
        desc = ' '.join(desc_parts).strip()
        amount, trntype = _sant_parse_amount(value_str, sign_char)
        transactions.append({'date': current_date, 'desc': desc,
                             'amount': amount, 'trntype': trntype})
        desc_parts, doc_num = [], None

    for line in lines:
        stripped = line.strip()
        if not stripped: continue

        # Activate table after opening-balance line
        if not in_table:
            if re.match(r'^SALDO EM \d{2}/\d{2}$', stripped):
                in_table = True
            continue

        # Hard stop on closing balance
        if SANT_STOP_RE.match(stripped) and stripped != lines[1]:
            # Only stop on the CLOSING saldo (not the opening one)
            # The opening one activates in_table above; if we're already in_table
            # and see SALDO EM again, it's the closing balance
            flush('0,00', ' ')   # flush any pending (shouldn't happen)
            break

        if _sant_is_noise(stripped): continue

        # Date line: "01/08" → full date YYYYMMDD
        if SANT_DATE_RE.match(stripped):
            d, mo = stripped.split('/')[:2]
            mo = mo.strip()
            current_date = f"{year}{mo.zfill(2)}{d.zfill(2)}"
            continue

        # Doc line (pure digits or dash) — only valid when we have desc
        if SANT_DOC_RE.match(stripped) and desc_parts:
            doc_num = stripped
            continue

        # Value line
        vm = SANT_VALUE_RE.match(stripped)
        if vm:
            num_str  = vm.group(1)
            sign_chr = vm.group(2).strip() or ' '
            if doc_num is not None:
                # Transaction value
                flush(num_str, sign_chr)
            # else: daily balance line → skip
            continue

        # Description line
        if doc_num is None:
            desc_parts.append(stripped)

    return {'agency': agency, 'account': account, 'transactions': transactions}


# ═══════════════════════════════════════════════
#  FORMAT ITAU — "Lançamentos do período"
#  Value-anchored: DATE → desc lines → [CNPJ/CPF] → VALUE
# ═══════════════════════════════════════════════

ITAU_DATE_RE  = re.compile(r'^\d{2}/\d{2}/\d{4}$')
ITAU_VALUE_RE = re.compile(r'^-?[\d.]+,\d{2}$')
ITAU_DOC_RE   = re.compile(r'^\d{2,3}\.\d{3}\.\d{3}[-/]')  # CNPJ or CPF

ITAU_SKIP_DESC = {
    'saldo total disponível dia', 'saldo anterior',
    'data', 'lançamentos', 'razão social',
    'cnpj/cpf', 'valor (r$)', 'saldo (r$)',
}
ITAU_STOP_STARTS = (
    'aviso:', 'os saldos acima', 'em caso de dúvidas',
    'atualizado em', 'reclamações, informações',
)

def _itau_is_stop(line):
    lo = line.lower().strip()
    return any(lo.startswith(s) for s in ITAU_STOP_STARTS)

def _parse_itau(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text('text') for i in range(len(doc))]
    doc.close()
    full = '\n'.join(pages)

    agency, account = '', ''
    m = re.search(r'Ag[eê]ncia\s*\n[\s\n]*(\d+)\s*Conta\s*([\d\-]+)', full, re.I)
    if m:
        agency  = m.group(1).strip()
        account = re.sub(r'[^\d]', '', m.group(2))

    lines = [l.strip() for l in full.splitlines()]
    transactions = []
    current_date = None
    desc_parts   = []

    def flush(amount_str):
        nonlocal current_date, desc_parts
        if not current_date or not desc_parts:
            current_date, desc_parts = None, []
            return
        desc = ' '.join(desc_parts).strip()
        if desc.lower() in ITAU_SKIP_DESC or desc.lower().startswith('saldo'):
            current_date, desc_parts = None, []
            return
        neg    = amount_str.startswith('-')
        v      = float(amount_str.lstrip('-').replace('.', '').replace(',', '.'))
        amount = -v if neg else v
        transactions.append({'date': current_date, 'desc': desc,
                              'amount': amount,
                              'trntype': 'DEBIT' if neg else 'CREDIT'})
        current_date, desc_parts = None, []

    for line in lines:
        if not line: continue
        if _itau_is_stop(line): break
        if ITAU_DATE_RE.match(line):
            current_date = parse_date(line)
            desc_parts   = []
            continue
        if current_date is None: continue
        if ITAU_DOC_RE.match(line): continue          # skip CNPJ/CPF
        if ITAU_VALUE_RE.match(line):
            flush(line)
            continue
        desc_parts.append(line)

    return {'agency': agency, 'account': account, 'transactions': transactions}


# ═══════════════════════════════════════════════
#  FORMAT ITAU 2 — "extrato mensal" (monthly layout)
#  Line-anchored: desc line → value line, alternating.
#  Date DD/MM appears once per day group; year from page
#  header "ag NNNN cc NNNNN-N abr 2024".
#  Value column (entradas/saídas/saldo) resolved by x-position
#  against the per-page column headers; saldo column skipped.
#  Acronym sidebar on page 0 filtered by x-position (left of
#  the "data" column).
# ═══════════════════════════════════════════════

ITAU2_HEADER_RE = re.compile(
    r'^ag\s+(\d+)\s+cc\s+([\d\-]+)\s+([a-zç]{3})\s+(\d{4})', re.I)
ITAU2_DATE_RE   = re.compile(r'^\d{2}/\d{2}$')
ITAU2_VALUE_RE  = re.compile(r'^[\d.]+,\d{2}-?$')
ITAU2_FOOTER_RE = re.compile(r'^\d{5,}\s+\w+\s+\d{2}/\d{2}/\d{4}\s')  # "254758 B001A 04/05/2024 ..."

ITAU2_MONTHS = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
                'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}

ITAU2_SKIP_LINES = {
    'extrato mensal', 'data', 'descrição', 'entradas r$', 'saídas r$',
    'saldo r$', '(créditos)', '(débitos)',
}
ITAU2_SKIP_STARTS = ('este material está disponível',)
ITAU2_STOP_STARTS = ('saldo final', 'totalizador de aplica')


def _itau2_is_stop(lo):
    """True if `lo` (normalised, lower-cased line) is an end-of-statement marker.

    'Saldo final' marks the closing balance at the end of the movement table.
    It must NOT match the per-day 'Saldo final disponivel' line (the balance
    released from discounted bills / títulos em cobrança), which appears
    mid-statement and would otherwise truncate the statement early.
    """
    if lo.startswith('totalizador de aplica'):
        return True
    if lo.startswith('saldo final') and 'dispon' not in lo:
        return True
    return False

# Fallback column anchors (right edge); overwritten per page from headers
ITAU2_DEFAULT_COLS = {'ent': 397.0, 'sai': 457.0, 'sal': 550.0}


def _itau2_page_lines(page):
    """Yield (x0, x1, text) per visual line, in stream order."""
    out = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            txt = ''.join(s['text'] for s in line['spans']).strip()
            if txt:
                bbox = line['bbox']
                out.append((bbox[0], bbox[2], txt))
    return out


def _parse_itau_mensal(pdf_path):
    doc = fitz.open(pdf_path)
    pages = [_itau2_page_lines(doc[i]) for i in range(len(doc))]
    doc.close()

    agency, account = '', ''
    stmt_month, stmt_year = None, None
    transactions = []
    current_date = None
    desc_parts   = []
    in_table     = False
    done         = False
    cols         = dict(ITAU2_DEFAULT_COLS)
    data_x0      = 148.0   # left edge of "data" column; sidebar lines end left of it

    def flush(amount, trntype):
        nonlocal desc_parts
        desc = ' '.join(desc_parts).strip()
        desc_parts = []
        if not current_date or not desc:
            return
        if desc.lower().startswith('saldo'):
            return
        transactions.append({'date': current_date, 'desc': desc,
                             'amount': amount, 'trntype': trntype})

    for page_lines in pages:
        if done:
            break
        # column anchors from this page's headers
        # (scan stops at the section end — later tables on the same page,
        #  e.g. "totalizador de aplicações automáticas", reuse header names
        #  like "data" at different x-positions)
        for x0, x1, txt in page_lines:
            lo = txt.lower().strip()
            if _itau2_is_stop(re.sub(r'\s+', ' ', lo)):
                break
            if lo == 'entradas r$':
                cols['ent'] = x1
            elif lo == 'saídas r$':
                cols['sai'] = x1
            elif re.sub(r'\s+', ' ', lo) == 'saldo r$':
                cols['sal'] = x1
            elif lo == 'data':
                data_x0 = x0 - 2

        for x0, x1, txt in page_lines:
            lo = re.sub(r'\s+', ' ', txt.lower().strip())

            m = ITAU2_HEADER_RE.match(txt)
            if m:
                if not agency:
                    agency  = m.group(1)
                    account = re.sub(r'[^\d]', '', m.group(2))
                if stmt_year is None:
                    stmt_month = ITAU2_MONTHS.get(m.group(3).lower())
                    stmt_year  = int(m.group(4))
                continue
            if not in_table:
                if lo.startswith('conta corrente | movimentação'):
                    in_table = True
                continue
            if _itau2_is_stop(lo):
                done = True
                break
            if lo in ITAU2_SKIP_LINES or any(lo.startswith(s) for s in ITAU2_SKIP_STARTS):
                continue
            if ITAU2_FOOTER_RE.match(txt):
                continue
            if x1 < data_x0:        # acronym sidebar (page 0)
                continue

            if ITAU2_DATE_RE.match(txt):
                dd, mm = int(txt[:2]), int(txt[3:5])
                year = stmt_year or datetime.now().year
                if stmt_month and mm > stmt_month:   # "Saldo anterior" from previous year
                    year -= 1
                current_date = f'{year:04d}{mm:02d}{dd:02d}'
                desc_parts = []
                continue

            if ITAU2_VALUE_RE.match(txt):
                # classify by nearest column right edge
                col = min(cols, key=lambda k: abs(cols[k] - x1))
                if col == 'sal':                     # saldo column → not a transaction
                    desc_parts = []
                    continue
                v = float(txt.rstrip('-').replace('.', '').replace(',', '.'))
                if col == 'sai':
                    flush(-v, 'DEBIT')
                else:
                    flush(v, 'CREDIT')
                continue

            desc_parts.append(txt)

    return {'agency': agency, 'account': account, 'transactions': transactions}


# ═══════════════════════════════════════════════
#  Auto-detecting dispatcher
# ═══════════════════════════════════════════════

def parse_bb(pdf_path):
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed.")
    doc = fitz.open(pdf_path)
    first_line = doc[0].get_text('text').strip().splitlines()[0].strip()
    doc.close()
    if 'Consultas' in first_line:
        return _parse_bb2(pdf_path)
    return _parse_bb1(pdf_path)

def parse_mp(pdf_path):
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed.")
    return _parse_mp(pdf_path)

def parse_bradesco(pdf_path):
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed.")
    return _parse_bradesco(pdf_path)

def parse_btg(pdf_path):
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed.")
    return _parse_btg(pdf_path)

def parse_santander(pdf_path):
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed.")
    return _parse_santander(pdf_path)

def parse_itau(pdf_path):
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed.")
    doc = fitz.open(pdf_path)
    page0 = doc[0].get_text('text')
    doc.close()
    first_line = page0.strip().splitlines()[0].strip().lower()
    if first_line == 'extrato mensal':
        return _parse_itau_mensal(pdf_path)
    return _parse_itau(pdf_path)

PARSERS = {'bb': parse_bb, 'mp': parse_mp, 'bradesco': parse_bradesco,
           'btg': parse_btg, 'santander': parse_santander, 'itau': parse_itau}


# ═══════════════════════════════════════════════
#  OFX generator  (shared by both formats)
# ═══════════════════════════════════════════════

def _write_ofx(bankid, fid, org, agency, account, txns, dt_start=None, dt_end=None):
    """Core OFX 1.02 SGML writer. Returns the file content string (or None)."""
    if not txns: return None
    agency  = agency  or '0001'
    account = account or '00000000'

    dates    = sorted(t['date'] for t in txns)
    dt_start = dt_start or dates[0]
    dt_end   = dt_end   or dates[-1]

    counter = {}
    def fitid(d):
        counter[d] = counter.get(d, 0) + 1
        return f"{d}{counter[d]:02d}"

    L = []
    def ln(s): L.append(s)

    ln('OFXHEADER:100'); ln('DATA:OFXSGML'); ln('VERSION:102')
    ln('SECURITY:NONE'); ln('ENCODING:USASCII'); ln('CHARSET:1252')
    ln('COMPRESSION:NONE'); ln('OLDFILEUID:NONE'); ln('NEWFILEUID:NONE')
    ln('<OFX>')
    ln('<SIGNONMSGSRSV1>'); ln('<SONRS>')
    ln('<STATUS>'); ln('<CODE>0</CODE>'); ln('<SEVERITY>INFO</SEVERITY>'); ln('</STATUS>')
    ln(f'<DTSERVER>{dt_end}235959</DTSERVER>')
    ln('<LANGUAGE>POR</LANGUAGE>')
    ln('<FI>'); ln(f'<ORG>{org}</ORG>'); ln(f'<FID>{fid}</FID>'); ln('</FI>')
    ln('</SONRS>'); ln('</SIGNONMSGSRSV1>')
    ln('<BANKMSGSRSV1>'); ln('<STMTTRNRS>'); ln('<TRNUID>1</TRNUID>')
    ln('<STATUS>'); ln('<CODE>0</CODE>'); ln('<SEVERITY>INFO</SEVERITY>'); ln('</STATUS>')
    ln('<STMTRS>'); ln('<CURDEF>BRL</CURDEF>')
    ln('<BANKACCTFROM>')
    ln(f'<BANKID>{bankid}</BANKID>')
    ln(f'<BRANCHID>{agency}</BRANCHID>')
    ln(f'<ACCTID>{account}</ACCTID>')
    ln('<ACCTTYPE>CHECKING</ACCTTYPE>')
    ln('</BANKACCTFROM>')
    ln('<BANKTRANLIST>')
    ln(f'<DTSTART>{dt_start}</DTSTART>'); ln(f'<DTEND>{dt_end}</DTEND>')

    for t in txns:
        fid_v = fitid(t['date'])
        ln('<STMTTRN>')
        ln(f'<TRNTYPE>{t["trntype"]}</TRNTYPE>')
        ln(f'<DTPOSTED>{t["date"]}</DTPOSTED>')
        ln(f'<TRNAMT>{t["amount"]:.2f}</TRNAMT>')
        ln(f'<FITID>{fid_v}</FITID>'); ln(f'<CHECKNUM>{fid_v}</CHECKNUM>')
        ln(f'<MEMO>{t["desc"]}</MEMO>')
        ln('</STMTTRN>')

    ln('</BANKTRANLIST>')
    ln('<LEDGERBAL>'); ln('<BALAMT>0.00</BALAMT>'); ln(f'<DTASOF>{dt_end}</DTASOF>'); ln('</LEDGERBAL>')
    ln('</STMTRS>'); ln('</STMTTRNRS>'); ln('</BANKMSGSRSV1>'); ln('</OFX>')

    return '\n'.join(L)


def _safe(s):
    return re.sub(r'_+', '_', re.sub(r'[^\w]', '_', (s or '').lower().strip())).strip('_')


def generate_ofx(bank_key, company, data, period_label=None):
    bank    = BANKS[bank_key]
    txns    = data['transactions']
    agency  = data.get('agency')  or '0001'
    account = data.get('account') or '00000000'
    if not txns: return None, None

    dates    = sorted(t['date'] for t in txns)
    dt_start = data.get('dt_start') or dates[0]
    dt_end   = data.get('dt_end')   or dates[-1]

    content = _write_ofx(bank["bankid"], bank["fid"], bank["org"],
                         agency, account, txns, dt_start, dt_end)
    period  = period_label or dt_end[:6]
    return content, f"{bank_key}_{_safe(company)}_{_safe(period)}.ofx"


# ═══════════════════════════════════════════════
#  CSV generator  (shared by all parsers)
# ═══════════════════════════════════════════════

def generate_csv(bank_key, company, data, period_label=None):
    import csv, io as _io
    txns    = data['transactions']
    account = data.get('account') or ''
    if not txns: return None, None

    dates    = sorted(t['date'] for t in txns)
    dt_end   = data.get('dt_end') or dates[-1]

    buf = _io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)

    # Header row
    writer.writerow(['Data', 'Descrição', 'Valor', 'Tipo', 'Conta'])

    for t in txns:
        # Format date as DD/MM/YYYY
        d = t['date']
        date_fmt = f"{d[6:8]}/{d[4:6]}/{d[:4]}"
        writer.writerow([
            date_fmt,
            t['desc'],
            f"{t['amount']:.2f}".replace('.', ','),
            'Crédito' if t['trntype'] == 'CREDIT' else 'Débito',
            account,
        ])

    content = buf.getvalue()
    period   = period_label or dt_end[:6]
    safe_co  = re.sub(r'_+', '_', re.sub(r'[^\w]', '_', company.lower().strip())).strip('_')
    filename = f"{bank_key}_{safe_co}_{period}.csv"
    return content, filename


# ═══════════════════════════════════════════════
#  OFX / CSV parsing + merge helpers
# ═══════════════════════════════════════════════

# bankid → bank_key reverse lookup (for preserving FI metadata on merge)
BANKID_TO_KEY = {b['bankid']: k for k, b in BANKS.items()}
# also map zero-stripped variants (e.g. "0237" vs "237")
for _k, _b in list(BANKS.items()):
    BANKID_TO_KEY.setdefault(_b['bankid'].lstrip('0'), _k)


def _ofx_tag(name, s):
    """First value of an SGML/XML tag (handles <TAG>val and <TAG>val</TAG>)."""
    m = re.search(rf'<{name}>\s*([^<\r\n]*)', s, re.I)
    return m.group(1).strip() if m else ''


def parse_ofx(content_bytes):
    """Parse an OFX 1.x SGML (or 2.x XML) bank statement into the standard dict.
    Returns {bankid, agency, account, transactions:[{date,desc,amount,trntype}]}."""
    if isinstance(content_bytes, bytes):
        text = content_bytes.decode('latin-1', errors='replace')
    else:
        text = content_bytes

    acct_blk = ''
    m = re.search(r'<BANKACCTFROM>(.*?)</BANKACCTFROM>', text, re.I | re.S)
    if m: acct_blk = m.group(1)
    bankid  = _ofx_tag('BANKID',   acct_blk) or _ofx_tag('BANKID',  text)
    agency  = _ofx_tag('BRANCHID', acct_blk) or _ofx_tag('BRANCHID', text)
    account = _ofx_tag('ACCTID',   acct_blk) or _ofx_tag('ACCTID',   text)

    txns = []
    for blk in re.findall(r'<STMTTRN>(.*?)</STMTTRN>', text, re.I | re.S):
        dt   = re.sub(r'\D', '', _ofx_tag('DTPOSTED', blk))[:8]
        amt  = _ofx_tag('TRNAMT', blk)
        memo = _ofx_tag('MEMO', blk) or _ofx_tag('NAME', blk)
        ttype = (_ofx_tag('TRNTYPE', blk) or '').upper()
        if len(dt) != 8 or not amt:
            continue
        try:
            amount = float(amt.replace(',', '.'))
        except ValueError:
            continue
        if not ttype:
            ttype = 'DEBIT' if amount < 0 else 'CREDIT'
        txns.append({'date': dt, 'desc': memo, 'amount': amount, 'trntype': ttype})

    return {'bankid': bankid, 'agency': agency, 'account': account, 'transactions': txns}


def parse_csv_content(content_bytes):
    """Parse a CSV produced by this app (Data,Descrição,Valor,Tipo,Conta)."""
    import csv as _csv
    if isinstance(content_bytes, bytes):
        text = content_bytes.decode('utf-8-sig', errors='replace')
    else:
        text = content_bytes

    sample = text[:2000]
    delim = ';' if sample.count(';') > sample.count(',') else ','
    rows = list(_csv.reader(io.StringIO(text), delimiter=delim))

    txns, account = [], ''
    for r in rows:
        if len(r) < 4:
            continue
        d, desc, val, tipo = r[0].strip(), r[1].strip(), r[2].strip(), r[3].strip()
        acct = r[4].strip() if len(r) > 4 else ''
        dm = re.match(r'(\d{2})/(\d{2})/(\d{4})', d)
        if not dm:
            continue   # skips header row and blanks
        date = f"{dm.group(3)}{dm.group(2)}{dm.group(1)}"
        try:
            amount = float(val.replace('.', '').replace(',', '.'))
        except ValueError:
            continue
        tl = tipo.lower()
        if tl.startswith('déb') or tl.startswith('deb') or amount < 0:
            trntype = 'DEBIT'
        else:
            trntype = 'CREDIT'
        if acct:
            account = acct
        txns.append({'date': date, 'desc': desc, 'amount': amount, 'trntype': trntype})

    return {'bankid': '', 'agency': '', 'account': account, 'transactions': txns}


def _flatten(data):
    """Yield (account, agency, [txns]) from any parser result (handles 'monthly')."""
    if 'monthly' in data:
        txns = [t for _, ms in sorted(data['monthly'].items()) for t in ms]
        yield (data.get('account') or '', data.get('agency') or '', txns)
    else:
        yield (data.get('account') or '', data.get('agency') or '',
               data.get('transactions') or [])


def _dedupe(txns):
    """Sort by date and drop exact duplicates (date + amount + normalised desc)."""
    seen, out = set(), []
    for t in sorted(txns, key=lambda x: x['date']):
        key = (t['date'], round(t['amount'], 2), ' '.join(t['desc'].lower().split()))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


# ═══════════════════════════════════════════════
#  Flask routes
# ═══════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html', banks=BANKS, version=APP_VERSION)

@app.route('/convert', methods=['POST'])
def convert():
    company    = request.form.get('company', '').strip()
    bank_key   = request.form.get('bank', 'bb')
    out_format = request.form.get('format', 'ofx')        # 'ofx' or 'csv'
    # Unified grouping (applies to OFX and CSV). 'csv_grouping' kept for compat.
    grouping   = request.form.get('grouping') or request.form.get('csv_grouping', 'month')
    files      = request.files.getlist('pdfs')

    if not company:
        return jsonify({'error': 'Informe o nome da empresa.'}), 400
    if not files or not files[0].filename:
        return jsonify({'error': 'Nenhum arquivo PDF enviado.'}), 400
    if bank_key not in BANKS:
        return jsonify({'error': 'Banco inválido.'}), 400
    parser = PARSERS.get(bank_key)
    if not parser:
        return jsonify({'error': f'Parser não disponível para {bank_key}.'}), 400

    encode    = 'utf-8-sig' if out_format == 'csv' else 'latin-1'
    generator = generate_csv if out_format == 'csv' else generate_ofx
    ext       = 'csv' if out_format == 'csv' else 'ofx'
    ok, errs  = [], []

    # ── Parse every PDF up front ──────────────────────────────────────
    parsed = []   # list of parser-result dicts
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            name = f.filename or ''
            if not name.lower().endswith('.pdf'):
                if name: errs.append(f"{name}: não é um PDF.")
                continue
            path = os.path.join(tmp, os.path.basename(name))
            f.save(path)
            try:
                parsed.append(parser(path))
            except Exception as e:
                errs.append(f"{name}: {e}")

    # ── SINGLE: merge everything into one file per account ────────────
    if grouping == 'single':
        from collections import OrderedDict
        groups = OrderedDict()   # account -> {'agency':str, 'txns':[]}
        for data in parsed:
            for account, agency, txns in _flatten(data):
                g = groups.setdefault(account, {'agency': agency, 'txns': []})
                if not g['agency'] and agency:
                    g['agency'] = agency
                g['txns'].extend(txns)

        multi = len([a for a, g in groups.items() if g['txns']]) > 1
        for account, g in groups.items():
            txns = _dedupe(g['txns'])
            if not txns:
                continue
            label = f"{txns[0]['date'][:6]}_{txns[-1]['date'][:6]}"
            if multi and account:
                label = f"{account}_{label}"
            flat = {'agency': g['agency'], 'account': account, 'transactions': txns,
                    'dt_start': txns[0]['date'], 'dt_end': txns[-1]['date']}
            content, fname = generator(bank_key, company, flat, period_label=label)
            if content:
                ok.append((fname, content.encode(encode)))

    # ── MONTH (default): one file per source / per month ──────────────
    else:
        for data in parsed:
            if 'monthly' in data:
                if not data['monthly']:
                    errs.append("Nenhuma transação encontrada.")
                    continue
                for ym, txns in sorted(data['monthly'].items()):
                    flat = {'agency': data['agency'], 'account': data['account'],
                            'transactions': txns,
                            'dt_start': txns[0]['date'], 'dt_end': txns[-1]['date']}
                    content, fname = generator(bank_key, company, flat)
                    if content:
                        ok.append((fname, content.encode(encode)))
            else:
                content, fname = generator(bank_key, company, data)
                if content is None:
                    errs.append("Nenhuma transação encontrada.")
                    continue
                ok.append((fname, content.encode(encode)))

    if not ok:
        return jsonify({'error': errs[0] if errs else 'Falha na conversão.'}), 400

    return _send_results(ok, errs)


def _send_results(ok, errs, zip_name='ofx_convertidos.zip'):
    """Send a single file directly, or a zip when there are several outputs."""
    if len(ok) == 1 and not errs:
        fname, data = ok[0]
        mime = 'text/csv' if fname.endswith('.csv') else 'application/x-ofx'
        return send_file(io.BytesIO(data), as_attachment=True,
                         download_name=fname, mimetype=mime)

    used = {}
    buf  = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname, data in ok:
            # guarantee unique entry names inside the archive
            n = used.get(fname, 0)
            used[fname] = n + 1
            if n:
                stem, dot, extn = fname.rpartition('.')
                fname = f"{stem}_{n+1}.{extn}" if dot else f"{fname}_{n+1}"
            zf.writestr(fname, data)
        if errs:
            zf.writestr('_erros.txt', '\n'.join(errs))
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=zip_name, mimetype='application/zip')


@app.route('/merge', methods=['POST'])
def merge():
    """Merge uploaded .ofx / .csv files into one OFX per account (deduped)."""
    company = request.form.get('company', '').strip()
    files   = request.files.getlist('files')

    if not files or not files[0].filename:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    from collections import OrderedDict
    groups = OrderedDict()   # account -> {'agency':str, 'bankid':str, 'txns':[]}
    errs   = []

    for f in files:
        name = f.filename or ''
        low  = name.lower()
        try:
            raw = f.read()
            if low.endswith('.ofx'):
                data = parse_ofx(raw)
            elif low.endswith('.csv'):
                data = parse_csv_content(raw)
            else:
                if name: errs.append(f"{name}: tipo não suportado (use .ofx ou .csv).")
                continue
        except Exception as e:
            errs.append(f"{name}: {e}")
            continue

        if not data['transactions']:
            errs.append(f"{name}: nenhuma transação encontrada.")
            continue

        account = data.get('account') or ''
        g = groups.setdefault(account, {'agency': '', 'bankid': '', 'txns': []})
        if not g['agency'] and data.get('agency'):
            g['agency'] = data['agency']
        if not g['bankid'] and data.get('bankid'):
            g['bankid'] = data['bankid']
        g['txns'].extend(data['transactions'])

    ok    = []
    multi = len([a for a, g in groups.items() if g['txns']]) > 1
    for account, g in groups.items():
        txns = _dedupe(g['txns'])
        if not txns:
            continue
        bankid = g['bankid'] or '000'
        key    = BANKID_TO_KEY.get(bankid) or BANKID_TO_KEY.get(bankid.lstrip('0'))
        if key:
            b = BANKS[key]
            bankid, fid, org, prefix = b['bankid'], b['fid'], b['org'], key
        else:
            fid, org, prefix = bankid, 'Banco', 'merged'
        content = _write_ofx(bankid, fid, org, g['agency'], account, txns,
                             txns[0]['date'], txns[-1]['date'])
        if not content:
            continue
        label = f"{txns[0]['date'][:6]}_{txns[-1]['date'][:6]}"
        if multi and account:
            label = f"{account}_{label}"
        co    = _safe(company) or 'merge'
        fname = f"{prefix}_{co}_{_safe(label)}.ofx"
        ok.append((fname, content.encode('latin-1')))

    if not ok:
        return jsonify({'error': errs[0] if errs else 'Nenhuma transação para mesclar.'}), 400

    return _send_results(ok, errs, zip_name='ofx_mesclados.zip')


@app.route('/shutdown', methods=['POST'])
def shutdown():
    import threading, os, signal
    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return ('', 204)

if __name__ == '__main__':
    print('▶  http://localhost:5050')
    app.run(debug=False, port=5050)
