from decimal import Decimal

import pytest

from gzp_finance.importers import ImportError, parse_statement

MI = "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n01/04/2026;02/04/2026;Fondo ejemplo;-1.299,87;EUR\n"
TR_HEADER = "datetime,date,type,name,symbol,amount,fee,tax,currency,transaction_id,account_type,asset_class\n"
TR = (TR_HEADER + "2026-04-01T12:00:00Z,2026-04-01,BUY,Fondo ejemplo,EXAMPLE,-99.50,-0.50,,EUR,id-a,CASH,ETF\n"
      "2026-04-01T12:00:02Z,2026-04-01,BUY,Fondo ejemplo,EXAMPLE,-50.00,,,-EUR,id-b,CASH,ETF\n").replace("-EUR", "EUR")
CAIXA = b'''<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Movimientos"><Table>
<Row><Cell ss:Index="2"><Data ss:Type="String">Fecha</Data></Cell><Cell><Data ss:Type="String">Concepto</Data></Cell><Cell><Data ss:Type="String">Categor&#237;a</Data></Cell><Cell><Data ss:Type="String">Importe (&#8364;)</Data></Cell><Cell><Data ss:Type="String">Tipo Movimiento</Data></Cell><Cell><Data ss:Type="String">Cuenta/Tarjeta</Data></Cell></Row>
<Row><Cell ss:Index="2"><Data ss:Type="String">01/04/2026</Data></Cell><Cell><Data ss:Type="String">Compra ejemplo</Data></Cell><Cell><Data ss:Type="String">Compras</Data></Cell><Cell><Data ss:Type="Number">-10.25</Data></Cell><Cell><Data ss:Type="String">Gasto (G)</Data></Cell><Cell><Data ss:Type="String">Cuenta ejemplo</Data></Cell></Row>
<Row><Cell ss:Index="2"><Data ss:Type="String">Total Gastos (importe en &#8364;)</Data></Cell><Cell><Data ss:Type="Number">10.25</Data></Cell></Row></Table></Worksheet></Workbook>'''


def test_myinvestor_decimal_dates_and_raw_preserved():
    txs, _ = parse_statement("MyInvestor", MI.encode())
    assert txs[0]["amount_eur"] == Decimal("-1299.87")
    assert str(txs[0]["value_date"]) == "2026-04-02"
    assert txs[0]["direction"] == "gasto"
    assert txs[0]["raw_payload"]["Importe"] == "-1.299,87"


def test_trade_fills_net_and_component_ids():
    txs, counts = parse_statement("Trade Republic", TR.encode())
    assert len(txs) == 1
    assert txs[0]["amount_eur"] == Decimal("-150.00")
    assert counts["grouped_fills"] == 1
    assert len(txs[0]["raw_payload"]["rows"]) == 2


def test_separate_trade_orders_or_accounts_are_not_grouped():
    data = TR.replace("12:00:02", "12:00:03")
    assert len(parse_statement("Trade Republic", data.encode())[0]) == 2
    data = TR.replace("id-b,CASH", "id-b,OTHER")
    assert len(parse_statement("Trade Republic", data.encode())[0]) == 2


def test_non_cash_migration_is_explicitly_skipped():
    data = TR_HEADER + "2026-04-01T12:00:00,2026-04-01,MIGRATION,Fondo,TEST,0,,,EUR,id,CASH,ETF\n"
    txs, counts = parse_statement("Trade Republic", data.encode())
    assert txs == [] and counts["skipped_non_cash"] == 1


def test_caixa_sparse_cells_and_footer():
    txs, _ = parse_statement("CaixaBank", CAIXA)
    assert len(txs) == 1
    assert txs[0]["amount_eur"] == Decimal("-10.25")
    assert txs[0]["bank_type"] == "Gasto (G)"
    assert txs[0]["account_ref"] == "Cuenta ejemplo"


def test_same_day_identical_payments_retain_multiplicity():
    data = MI + MI.splitlines()[1] + "\n"
    txs, _ = parse_statement("MyInvestor", data.encode())
    assert txs[0]["fingerprint"] == txs[1]["fingerprint"]
    assert [t["occurrence"] for t in txs] == [0, 1]


@pytest.mark.parametrize("bank,data", [("MyInvestor", MI.replace("EUR", "USD").encode()),
                                      ("MyInvestor", MI.replace("01/04/2026", "bad").encode()),
                                      ("Trade Republic", b"unexpected,columns\n1,2\n"),
                                      ("CaixaBank", b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')])
def test_malformed_exports_are_rejected(bank, data):
    with pytest.raises(ImportError):
        parse_statement(bank, data)
