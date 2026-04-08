import csv
from io import StringIO
from flask import Response
from models import Movimentacao

def gerar_csv_extrato(movimentacoes):
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Data', 'Descrição', 'Valor (R$)', 'Tipo', 'Comprovante'])
    for m in movimentacoes:
        writer.writerow([
            m.data_mov.strftime('%d/%m/%Y'),
            m.descricao,
            f"{m.valor:.2f}",
            'Entrada' if m.tipo == 'entrada' else 'Saída',
            m.comprovante_path or ''
        ])
    output = si.getvalue()
    return Response(output, mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=extrato_evento.csv'})