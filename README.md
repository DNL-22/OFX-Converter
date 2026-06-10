# PDF → OFX/CSV Converter

Conversor local de extratos bancários em PDF para os formatos OFX e CSV.  
Desenvolvido para escritórios contábeis que processam extratos de múltiplos clientes.

**v1.9.0** — Banco do Brasil · Bradesco · Santander · BTG Pactual · Mercado Pago · Itaú

---

## Funcionalidades

- Conversão individual ou em lote (múltiplos PDFs)
- Exportação para OFX (importação em sistemas contábeis) ou CSV (planilhas)
- CSV com agrupamento por mês ou arquivo único
- Detecção automática do formato do extrato por banco
- Processamento 100% local — nenhum arquivo é armazenado ou enviado externamente
- Interface web via navegador, sem necessidade de conhecimento técnico

## Bancos suportados

| Banco | Código ISPB |
|---|---|
| Banco do Brasil | 001 |
| Bradesco | 0237 |
| Santander | 033 |
| BTG Pactual | 208 |
| Mercado Pago | 323 |
| Itaú | 341 |

## Instalação

**Pré-requisito:** Python 3.8 ou superior.

```bash
git clone https://github.com/danielbertoldo/ofx-converter.git
cd ofx-converter
pip install -r requirements.txt
python app.py
```

Abra [http://localhost:5050](http://localhost:5050) no navegador.

## Como usar

1. Informe o **nome da empresa**
2. Selecione o **banco**
3. Escolha o **formato** (OFX ou CSV)
4. Arraste os PDFs ou clique para selecioná-los
5. Clique em **Converter**

Para múltiplos PDFs, os arquivos são entregues em `.zip`.

## Apoie o projeto

Se a ferramenta já te poupou tempo, considere apoiar o desenvolvimento. Cada contribuição ajuda a adicionar novos bancos e funcionalidades.

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Apoiar-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/danielbertoldo)

## Licença

Distribuído sob a [Elastic License 2.0](LICENSE). Uso comercial como serviço hospedado não é permitido sem autorização.
