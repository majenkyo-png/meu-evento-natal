import os
from datetime import date, datetime
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_file
from werkzeug.utils import secure_filename
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import qrcode
from io import BytesIO

from models import db, Usuario, DiaEvento, Refeicao, Movimentacao, ItemCompra, Parcela, Foto
from forms import MovimentacaoForm, ItemCompraForm, RefeicaoForm, LoginForm, PagamentoForm, FotoForm
from utils import gerar_csv_extrato

app = Flask(__name__)
app.config['SECRET_KEY'] = 'troque-esta-chave-por-uma-segura'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evento.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)

# --- Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para acessar o sistema.'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

# --- Proteção global: qualquer rota não listada exige login ---
@app.before_request
def before_request():
    rotas_publicas = ['login', 'cadastro', 'static']
    if request.endpoint in rotas_publicas:
        return None
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

# --- Inicializar dados (dias, refeições, saldo inicial) ---
def inicializar_dados():
    with app.app_context():
        ano_atual = date.today().year
        dias = [date(ano_atual, 12, 24), date(ano_atual, 12, 25),
                date(ano_atual, 12, 26), date(ano_atual, 12, 27)]
        for dia in dias:
            existe = DiaEvento.query.filter_by(data=dia).first()
            if not existe:
                novo_dia = DiaEvento(data=dia)
                db.session.add(novo_dia)
                db.session.commit()
                refs = ['cafe_manha', 'almoco', 'cafe_tarde', 'jantar']
                for ref in refs:
                    nova_ref = Refeicao(nome=ref, cardapio='A definir', dia_id=novo_dia.id)
                    db.session.add(nova_ref)
                db.session.commit()
        if Movimentacao.query.count() == 0:
            inicial = Movimentacao(descricao='Saldo inicial em caixa', valor=00.00, tipo='entrada', data_mov=date.today())
            db.session.add(inicial)
            db.session.commit()

# --- Rotas públicas (apenas login e cadastro) ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        usuario = Usuario.query.filter_by(email=email).first()
        if usuario and usuario.verificar_senha(senha):
            login_user(usuario)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('E-mail ou senha inválidos', 'danger')
    return render_template('login.html')

@app.route('/cadastro', methods=['GET', 'POST'])

def cadastro():
    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        telefone = request.form.get('telefone')
        if Usuario.query.filter_by(email=email).first():
            flash('E-mail já cadastrado. Faça login.', 'danger')
        else:
            # Primeiro usuário cadastrado se torna admin
            primeiro_usuario = Usuario.query.count() == 0
            novo = Usuario(nome=nome, email=email, telefone=telefone)
            novo.set_senha(senha)
            if primeiro_usuario:
                novo.is_admin = True
                flash('Primeiro usuário cadastrado como ADMINISTRADOR!', 'success')
            db.session.add(novo)
            db.session.commit()
            # Criar 9 parcelas (valor total R$ 450,00 -> R$ 50 cada)
            # Criar 9 parcelas de R$ 50,00 cada (total R$ 450,00)
            valor_parcela = 50.00
            valor_total = valor_parcela * 9   # 450.00
            for i in range(1, 10):
                data_venc = date(2025, i, 1) if i <= 12 else date(2026, i-12, 1)
                parcela = Parcela(
                    usuario_id=novo.id,
                    numero=i,
                    valor=valor_parcela,
                    data_vencimento=data_venc,
                    status='pendente'
                )
                db.session.add(parcela)
            db.session.commit()
            flash('Cadastro realizado! Faça login.', 'success')
            return redirect(url_for('login'))
    return render_template('cadastro.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Rotas protegidas (exigem login) ---
@app.route('/')
def index():
    entradas = db.session.query(db.func.sum(Movimentacao.valor)).filter_by(tipo='entrada').scalar() or 0
    saidas = db.session.query(db.func.sum(Movimentacao.valor)).filter_by(tipo='saida').scalar() or 0
    saldo = entradas - saidas
    valor_chacara = 3500.00
    meta_chacara = 1050.00
    total_arrecadado = entradas
    percentual_meta = min(50, (total_arrecadado / meta_chacara) * 50) if meta_chacara > 0 else 0

    # No app.py, dentro da função index(), substitua a parte do carrossel por:

    # Carrossel de fotos da chácara - lista todas as imagens da pasta
    fotos_chacara_dir = os.path.join('static', 'fotos_chacara')
    imagens_existentes = []
    if os.path.exists(fotos_chacara_dir):
        for arquivo in os.listdir(fotos_chacara_dir):
            if arquivo.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                imagens_existentes.append(os.path.join('fotos_chacara', arquivo).replace('\\', '/'))

    return render_template('index.html',
                           saldo=saldo,
                           valor_chacara=valor_chacara,
                           meta_chacara=meta_chacara,
                           total_arrecadado=total_arrecadado,
                           percentual_meta=percentual_meta,
                           imagens=imagens_existentes)

@app.route('/cardapios')
def cardapios():
    dias = DiaEvento.query.order_by(DiaEvento.data).all()
    estrutura = {}
    for dia in dias:
        estrutura[dia.data] = {
            'cafe_manha': next((r.cardapio for r in dia.refeicoes if r.nome == 'cafe_manha'), ''),
            'almoco': next((r.cardapio for r in dia.refeicoes if r.nome == 'almoco'), ''),
            'cafe_tarde': next((r.cardapio for r in dia.refeicoes if r.nome == 'cafe_tarde'), ''),
            'jantar': next((r.cardapio for r in dia.refeicoes if r.nome == 'jantar'), '')
        }
    return render_template('cardapios.html', estrutura=estrutura)

@app.route('/compras')
def compras():
    itens = ItemCompra.query.order_by(ItemCompra.comprado, ItemCompra.categoria, ItemCompra.nome).all()
    return render_template('compras.html', itens=itens)

@app.route('/comprovantes')
def comprovantes():
    movs = Movimentacao.query.filter(Movimentacao.comprovante_path.isnot(None)).order_by(Movimentacao.data_mov.desc()).all()
    entradas = db.session.query(db.func.sum(Movimentacao.valor)).filter_by(tipo='entrada').scalar() or 0
    saidas = db.session.query(db.func.sum(Movimentacao.valor)).filter_by(tipo='saida').scalar() or 0
    saldo = entradas - saidas
    return render_template('comprovantes.html', movimentacoes=movs, saldo=saldo)

@app.route('/extrato')
def extrato():
    movimentacoes = Movimentacao.query.order_by(Movimentacao.data_mov.desc()).all()
    entradas = sum(m.valor for m in movimentacoes if m.tipo == 'entrada')
    saidas = sum(m.valor for m in movimentacoes if m.tipo == 'saida')
    saldo = entradas - saidas
    return render_template('extrato.html', movimentacoes=movimentacoes, saldo=saldo, entradas=entradas, saidas=saidas)

@app.route('/extrato/download')
def extrato_download():
    movimentacoes = Movimentacao.query.order_by(Movimentacao.data_mov).all()
    return gerar_csv_extrato(movimentacoes)

# --- Rotas de parcelas (usuário logado) ---
@app.route('/minhas_parcelas')
def minhas_parcelas():
    parcelas = Parcela.query.filter_by(usuario_id=current_user.id).order_by(Parcela.numero).all()
    return render_template('minhas_parcelas.html', parcelas=parcelas)

@app.route('/pagar_parcela/<int:parcela_id>', methods=['GET', 'POST'])
def pagar_parcela(parcela_id):
    parcela = Parcela.query.get_or_404(parcela_id)
    if parcela.usuario_id != current_user.id:
        flash('Acesso negado a esta parcela.', 'danger')
        return redirect(url_for('minhas_parcelas'))
    form = PagamentoForm()
    if form.validate_on_submit():
        if form.comprovante.data:
            f = form.comprovante.data
            filename = secure_filename(f.filename)
            nome_unic = f"parc_{parcela_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], nome_unic)
            f.save(filepath)
            parcela.comprovante_path = f"uploads/{nome_unic}"
        parcela.status = 'pago'
        parcela.data_pagamento = date.today()
        parcela.observacao = form.observacao.data
        db.session.commit()
        flash('Comprovante enviado! Aguarde confirmação do organizador.', 'success')
        return redirect(url_for('minhas_parcelas'))
    return render_template('pagar_parcela.html', form=form, parcela=parcela)

def gerar_payload_pix(chave, nome, cidade, valor, txid):
    nome = nome[:25]
    cidade = cidade[:15]

    valor_str = f"{valor:.2f}"

    def monta_campo(id, valor):
        return f"{id}{len(valor):02d}{valor}"

    payload = ""
    payload += monta_campo("00", "01")

    # Merchant Account (PIX)
    gui = monta_campo("00", "BR.GOV.BCB.PIX")
    chave_campo = monta_campo("01", chave)
    merchant_account = monta_campo("26", gui + chave_campo)
    payload += merchant_account

    payload += monta_campo("52", "0000")
    payload += monta_campo("53", "986")
    payload += monta_campo("54", valor_str)
    payload += monta_campo("58", "BR")
    payload += monta_campo("59", nome)
    payload += monta_campo("60", cidade)

    txid_campo = monta_campo("05", txid)
    additional_data = monta_campo("62", txid_campo)
    payload += additional_data

    payload += "6304"

    # CRC16
    def crc16(payload):
        polinomio = 0x1021
        resultado = 0xFFFF
        for c in payload:
            resultado ^= ord(c) << 8
            for _ in range(8):
                if resultado & 0x8000:
                    resultado = (resultado << 1) ^ polinomio
                else:
                    resultado <<= 1
                resultado &= 0xFFFF
        return f"{resultado:04X}"

    return payload + crc16(payload)

@app.route('/gerar_qr_parcela/<int:parcela_id>')
@login_required
def gerar_qr_parcela(parcela_id):
    parcela = Parcela.query.get_or_404(parcela_id)

    if parcela.usuario_id != current_user.id:
        return "Acesso negado", 403

    chave_pix = "48204922841"
    nome = "Natal da Familia"
    cidade = "SAO PAULO"
    valor = float(parcela.valor)
    txid = f"PAR{parcela.id:06d}"

    payload = gerar_payload_pix(chave_pix, nome, cidade, valor, txid)

    import qrcode
    from io import BytesIO
    from flask import send_file

    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)

    return send_file(buf, mimetype='image/png')

# --- Área administrativa (apenas para usuários com is_admin=True) ---
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Acesso negado. Você não é administrador.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin')
@admin_required
def admin():
    return render_template('admin.html')

@app.route('/admin/parcelas')
@admin_required
def admin_parcelas():
    parcelas = Parcela.query.order_by(Parcela.status, Parcela.data_vencimento).all()
    return render_template('admin_parcelas.html', parcelas=parcelas)

@app.route('/admin/parcela/confirmar/<int:id>')
@admin_required
def confirmar_parcela(id):
    parcela = Parcela.query.get_or_404(id)
    if parcela.status != 'confirmado':
        parcela.status = 'confirmado'
        mov = Movimentacao(
            descricao=f'Pagamento parcela {parcela.numero} - {parcela.usuario.nome}',
            valor=parcela.valor,
            tipo='entrada',
            data_mov=date.today(),
            comprovante_path=parcela.comprovante_path
        )
        db.session.add(mov)
        db.session.commit()
        flash('Pagamento confirmado e lançado no caixa!', 'success')
    else:
        flash('Esta parcela já estava confirmada.', 'info')
    return redirect(url_for('admin_parcelas'))

@app.route('/admin/parcela/rejeitar/<int:id>')
@admin_required
def rejeitar_parcela(id):
    parcela = Parcela.query.get_or_404(id)
    parcela.status = 'pendente'
    parcela.comprovante_path = None
    parcela.data_pagamento = None
    db.session.commit()
    flash('Pagamento rejeitado. Peça para enviar novamente.', 'warning')
    return redirect(url_for('admin_parcelas'))

# Demais rotas admin (movimentações, itens, cardápios) também precisam de @admin_required
@app.route('/admin/movimentacao/nova', methods=['GET', 'POST'])
@admin_required
def nova_movimentacao():
    form = MovimentacaoForm()
    if form.validate_on_submit():
        comprovante_path = None
        if form.comprovante.data:
            f = form.comprovante.data
            filename = secure_filename(f.filename)
            nome_unic = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], nome_unic)
            f.save(filepath)
            comprovante_path = f"uploads/{nome_unic}"
        mov = Movimentacao(
            descricao=form.descricao.data,
            valor=form.valor.data,
            tipo=form.tipo.data,
            data_mov=form.data_mov.data,
            comprovante_path=comprovante_path
        )
        db.session.add(mov)
        db.session.commit()
        flash('Movimentação adicionada!', 'success')
        return redirect(url_for('extrato'))
    return render_template('admin_mov_form.html', form=form, titulo='Nova Movimentação')

@app.route('/admin/movimentacao/editar/<int:id>', methods=['GET', 'POST'])
@admin_required
def editar_movimentacao(id):
    mov = Movimentacao.query.get_or_404(id)
    form = MovimentacaoForm(obj=mov)
    if form.validate_on_submit():
        mov.descricao = form.descricao.data
        mov.valor = form.valor.data
        mov.tipo = form.tipo.data
        mov.data_mov = form.data_mov.data
        if form.comprovante.data:
            f = form.comprovante.data
            filename = secure_filename(f.filename)
            nome_unic = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], nome_unic)
            f.save(filepath)
            mov.comprovante_path = f"uploads/{nome_unic}"
        db.session.commit()
        flash('Movimentação atualizada!', 'success')
        return redirect(url_for('extrato'))
    return render_template('admin_mov_form.html', form=form, titulo='Editar Movimentação')

@app.route('/admin/movimentacao/deletar/<int:id>')
@admin_required
def deletar_movimentacao(id):
    mov = Movimentacao.query.get_or_404(id)
    db.session.delete(mov)
    db.session.commit()
    flash('Movimentação removida', 'warning')
    return redirect(url_for('extrato'))

@app.route('/admin/cardapio/editar/<int:dia_id>/<nome_refeicao>', methods=['GET', 'POST'])
@admin_required
def editar_cardapio(dia_id, nome_refeicao):
    refeicao = Refeicao.query.filter_by(dia_id=dia_id, nome=nome_refeicao).first_or_404()
    form = RefeicaoForm(obj=refeicao)
    if form.validate_on_submit():
        refeicao.cardapio = form.cardapio.data
        db.session.commit()
        flash('Cardápio atualizado!', 'success')
        return redirect(url_for('cardapios'))
    return render_template('admin_cardapio_form.html', form=form, refeicao=refeicao)

@app.route('/admin/item_compra/novo', methods=['GET', 'POST'])
@admin_required
def novo_item_compra():
    form = ItemCompraForm()
    if form.validate_on_submit():
        item = ItemCompra(
            nome=form.nome.data,
            quantidade=form.quantidade.data,
            categoria=form.categoria.data,
            comprado=form.comprado.data
        )
        db.session.add(item)
        db.session.commit()
        flash('Item adicionado à lista de compras', 'success')
        return redirect(url_for('compras'))
    return render_template('admin_item_form.html', form=form, titulo='Novo Item')

@app.route('/admin/item_compra/editar/<int:id>', methods=['GET', 'POST'])
@admin_required
def editar_item_compra(id):
    item = ItemCompra.query.get_or_404(id)
    form = ItemCompraForm(obj=item)
    if form.validate_on_submit():
        item.nome = form.nome.data
        item.quantidade = form.quantidade.data
        item.categoria = form.categoria.data
        item.comprado = form.comprado.data
        db.session.commit()
        flash('Item atualizado', 'success')
        return redirect(url_for('compras'))
    return render_template('admin_item_form.html', form=form, titulo='Editar Item')

@app.route('/admin/item_compra/deletar/<int:id>')
@admin_required
def deletar_item_compra(id):
    item = ItemCompra.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Item removido', 'warning')
    return redirect(url_for('compras'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logado', None)
    flash('Você saiu da área administrativa', 'info')
    return redirect(url_for('index'))

# --- (Opcional) Galeria de fotos separada – se quiser manter ---
@app.route('/fotos')
def fotos():
    todas_fotos = Foto.query.order_by(Foto.data_upload.desc()).all()
    return render_template('fotos.html', fotos=todas_fotos)

@app.route('/admin/foto/nova', methods=['GET', 'POST'])
@admin_required
def nova_foto():
    form = FotoForm()
    if form.validate_on_submit():
        f = form.imagem.data
        filename = secure_filename(f.filename)
        nome_unic = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], nome_unic)
        f.save(filepath)
        foto = Foto(
            titulo=form.titulo.data,
            descricao=form.descricao.data,
            imagem_path=f"uploads/{nome_unic}"
        )
        db.session.add(foto)
        db.session.commit()
        flash('Foto adicionada à galeria!', 'success')
        return redirect(url_for('fotos'))
    return render_template('admin_foto_form.html', form=form)

# --- Execução ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        inicializar_dados()
    app.run(debug=True)