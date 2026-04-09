import os
import binascii
from datetime import date, datetime
from flask import Flask, render_template, redirect, url_for, request, flash, session, send_file
from werkzeug.utils import secure_filename
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import qrcode
from io import BytesIO
import requests
import smtplib
from email.message import EmailMessage

from models import db, Usuario, DiaEvento, Refeicao, Movimentacao, ItemCompra, Parcela, Foto, Familiar
from forms import MovimentacaoForm, ItemCompraForm, RefeicaoForm, LoginForm, PagamentoForm, FotoForm, FamiliarForm
from utils import gerar_csv_extrato

app = Flask(__name__)
app.config['SECRET_KEY'] = 'troque-esta-chave-por-uma-segura'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evento.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)

# --- Configuração de e-mail ---
EMAIL_NOTIFICACOES = True
EMAIL_SMTP_SERVER = "smtp.gmail.com"
EMAIL_SMTP_PORT = 465
EMAIL_REMETENTE = "tokenrevise@gmail.com"
EMAIL_SENHA = "tayv xznr bfhd ewrc"
EMAIL_DESTINO = "majenkyo@gmail.com"

def enviar_email_notificacao(assunto, mensagem):
    if not EMAIL_NOTIFICACOES:
        return
    try:
        msg = EmailMessage()
        msg['Subject'] = assunto
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = EMAIL_DESTINO
        msg.set_content(mensagem)
        with smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT) as smtp:
            smtp.login(EMAIL_REMETENTE, EMAIL_SENHA)
            smtp.send_message(msg)
        print(f"E-mail enviado: {assunto}")
    except Exception as e:
        print(f"Falha ao enviar e-mail: {e}")

# --- Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para acessar o sistema.'

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

@app.before_request
def before_request():
    rotas_publicas = ['login', 'cadastro', 'static']
    if request.endpoint in rotas_publicas:
        return None
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

def inicializar_dados():
    with app.app_context():
        db.drop_all()  # <--- ISSO DELETA TODAS AS TABELAS
        db.create_all()  # <--- ISSO RECRIA AS TABELAS
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
            inicial = Movimentacao(descricao='Saldo inicial em caixa', valor=0.00, tipo='entrada', data_mov=date.today())
            db.session.add(inicial)
            db.session.commit()

# ================= FUNÇÕES PIX =================
def crc16(payload):
    crc = 0xFFFF
    for char in payload:
        crc ^= ord(char) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"

def gerar_payload_pix(chave_pix, valor, nome_recebedor="Natal da Familia", cidade="SAO PAULO", txid="***"):
    valor_str = f"{valor:.2f}"
    def tlv(id_tag, valor_tag):
        return f"{id_tag}{len(valor_tag):02d}{valor_tag}"
    gui = tlv("00", "br.gov.bcb.pix")
    key = tlv("01", chave_pix)
    merchant_account = tlv("26", gui + key)
    add_data = tlv("62", tlv("05", txid))
    payload = (
        tlv("00", "01") +
        merchant_account +
        tlv("52", "0000") +
        tlv("53", "986") +
        tlv("54", valor_str) +
        tlv("58", "BR") +
        tlv("59", nome_recebedor[:25]) +
        tlv("60", cidade[:15]) +
        add_data +
        "6304"
    )
    return payload + crc16(payload)
# ===============================================

# ========== FUNÇÃO AUXILIAR PARA CRIAR PARCELAS ==========
def criar_parcelas_para_pessoa(usuario_id, familiar_id, idade, nome_pessoa):
    """Cria 9 parcelas com valor baseado na idade:
       - 6 a 10 anos: R$ 25 (meia)
       - Maior que 10 anos: R$ 50 (inteira)
       - Menor que 6 anos: R$ 0 (não paga, mas registra)
    """
    if idade >= 6 and idade <= 10:
        valor_parcela = 25.00
        tipo = "meia"
    elif idade > 10:
        valor_parcela = 50.00
        tipo = "inteira"
    else:
        valor_parcela = 0.00
        tipo = "gratuito"
    
    # Se for gratuito (menor que 6 anos), já marca como confirmado
    status_inicial = "confirmado" if valor_parcela == 0 else "pendente"
    
    for i in range(1, 10):
        data_venc = date(2025, i, 1) if i <= 12 else date(2026, i-12, 1)
        parcela = Parcela(
            usuario_id=usuario_id,
            familiar_id=familiar_id,
            numero=i,
            valor=valor_parcela,
            data_vencimento=data_venc,
            status=status_inicial,
            observacao=f"{tipo} - {nome_pessoa}" if valor_parcela == 0 else None
        )
        db.session.add(parcela)
    db.session.commit()
# ========================================================

# --- Rotas de autenticação ---
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
            primeiro_usuario = Usuario.query.count() == 0
            novo = Usuario(nome=nome, email=email, telefone=telefone)
            novo.set_senha(senha)
            if primeiro_usuario:
                novo.is_admin = True
                flash('Primeiro usuário cadastrado como ADMINISTRADOR!', 'success')
            db.session.add(novo)
            db.session.commit()
            # Usuário responsável (maior de 10 anos, valor cheio)
            criar_parcelas_para_pessoa(novo.id, None, 30, nome)  # 30 anos como exemplo
            flash('Cadastro realizado! Faça login.', 'success')
            return redirect(url_for('login'))
    return render_template('cadastro.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Rotas principais ---
@app.route('/')
def index():
    entradas = db.session.query(db.func.sum(Movimentacao.valor)).filter_by(tipo='entrada').scalar() or 0
    saidas = db.session.query(db.func.sum(Movimentacao.valor)).filter_by(tipo='saida').scalar() or 0
    saldo = entradas - saidas
    valor_chacara = 500.00
    meta_chacara = 1050.00
    total_arrecadado = entradas
    percentual_meta = min(100, (total_arrecadado / meta_chacara) * 100) if meta_chacara > 0 else 0

    imagens_existentes = []
    fotos_dir = os.path.join('static', 'fotos_chacara')
    if os.path.exists(fotos_dir):
        for arquivo in os.listdir(fotos_dir):
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
        estrutura[dia.id] = {
            'data': dia.data,
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
    return render_template('comprovantes.html', movimentacoes=movs)

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

# --- Gerenciamento de Familiares ---
@app.route('/familiares')
@login_required
def listar_familiares():
    familiares = Familiar.query.filter_by(responsavel_id=current_user.id).all()
    return render_template('familiares.html', familiares=familiares)

@app.route('/familiares/novo', methods=['GET', 'POST'])
@login_required
def novo_familiar():
    form = FamiliarForm()
    if form.validate_on_submit():
        nome = form.nome.data
        idade = form.idade.data
        
        familiar = Familiar(
            responsavel_id=current_user.id,
            nome=nome,
            idade=idade
        )
        db.session.add(familiar)
        db.session.commit()
        
        # Criar parcelas para o familiar baseado na idade
        criar_parcelas_para_pessoa(current_user.id, familiar.id, idade, nome)
        
        flash(f'{nome} foi adicionado com sucesso!', 'success')
        return redirect(url_for('listar_familiares'))
    return render_template('familiar_form.html', form=form, titulo='Adicionar Familiar')

@app.route('/familiares/deletar/<int:id>')
@login_required
def deletar_familiar(id):
    familiar = Familiar.query.get_or_404(id)
    if familiar.responsavel_id != current_user.id:
        flash('Acesso negado.', 'danger')
        return redirect(url_for('listar_familiares'))
    
    # Deletar todas as parcelas associadas
    for parcela in familiar.parcelas:
        db.session.delete(parcela)
    db.session.delete(familiar)
    db.session.commit()
    flash(f'{familiar.nome} foi removido.', 'success')
    return redirect(url_for('listar_familiares'))

# --- Lista de Participantes (com separação por idade) ---
@app.route('/listar_participantes')
@login_required
def listar_participantes():
    # Participantes = usuário + seus familiares
    participantes = []
    
    # Adiciona o próprio usuário (responsável)
    participantes.append({
        'nome': current_user.nome,
        'idade': 30,  # idade padrão para responsável (maior que 10)
        'tipo': 'responsavel',
        'valor_parcela': 50.00,
        'pagou_alguma': any(p.status == 'confirmado' for p in current_user.parcelas if p.familiar_id is None)
    })
    
    # Adiciona os familiares
    for familiar in current_user.familiares:
        idade = familiar.idade
        if idade >= 6 and idade <= 10:
            valor_parcela = 25.00
        elif idade > 10:
            valor_parcela = 50.00
        else:
            valor_parcela = 0.00
        
        pagou_alguma = any(p.status == 'confirmado' for p in familiar.parcelas)
        
        participantes.append({
            'nome': familiar.nome,
            'idade': idade,
            'tipo': 'familiar',
            'valor_parcela': valor_parcela,
            'pagou_alguma': pagou_alguma
        })
    
    # Separar crianças (6 a 10 anos) e adultos (maior que 10 anos)
    criancas = [p for p in participantes if 6 <= p['idade'] <= 10]
    adultos = [p for p in participantes if p['idade'] > 10 or p['tipo'] == 'responsavel']
    menores_6 = [p for p in participantes if p['idade'] < 6]
    
    return render_template('listar_participantes.html', 
                           criancas=criancas, 
                           adultos=adultos,
                           menores_6=menores_6)

# --- Parcelas (PIX) - mostra parcelas do usuário e seus dependentes ---
@app.route('/minhas_parcelas')
@login_required
def minhas_parcelas():
    # Buscar parcelas do usuário (responsável)
    parcelas_proprias = Parcela.query.filter_by(usuario_id=current_user.id, familiar_id=None).order_by(Parcela.numero).all()
    
    # Buscar parcelas de todos os familiares
    parcelas_familiares = []
    for familiar in current_user.familiares:
        for parcela in familiar.parcelas:
            parcelas_familiares.append({
                'parcela': parcela,
                'nome_familiar': familiar.nome,
                'idade': familiar.idade
            })
    
    return render_template('minhas_parcelas.html', 
                           parcelas_proprias=parcelas_proprias,
                           parcelas_familiares=parcelas_familiares)

@app.route('/pagar_parcela/<int:parcela_id>', methods=['GET', 'POST'])
@login_required
def pagar_parcela(parcela_id):
    parcela = Parcela.query.get_or_404(parcela_id)
    
    # Verificar se o usuário tem permissão para pagar esta parcela
    if parcela.usuario_id != current_user.id:
        flash('Acesso negado a esta parcela.', 'danger')
        return redirect(url_for('minhas_parcelas'))
    
    form = PagamentoForm()
    chave_pix = "majenkyo@gmail.com"  # ALTERE PARA SUA CHAVE PIX
    try:
        payload = gerar_payload_pix(chave_pix, parcela.valor)
    except Exception as e:
        return f"ERRO PIX: {str(e)}"
        
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
        
        nome_pessoa = parcela.familiar.nome if parcela.familiar else current_user.nome
        assunto = f"Novo comprovante de pagamento PIX - Parcela {parcela.numero}"
        mensagem = f"Olá! {nome_pessoa} enviou comprovante da parcela {parcela.numero} (R$ {parcela.valor:.2f}).\nAcesse o admin para confirmar: https://meu-evento-natal-1.onrender.com/admin/parcelas"
        enviar_email_notificacao(assunto, mensagem)
        
        flash('Comprovante enviado! Aguarde confirmação do organizador.', 'success')
        return redirect(url_for('minhas_parcelas'))
    
    return render_template('pagar_parcela.html', form=form, parcela=parcela, payload=payload)

@app.route('/gerar_qr_parcela/<int:parcela_id>')
@login_required
def gerar_qr_parcela(parcela_id):
    parcela = Parcela.query.get_or_404(parcela_id)
    if parcela.usuario_id != current_user.id:
        return "Acesso negado", 403
    chave_pix = "majenkyo@gmail.com"
    payload = gerar_payload_pix(chave_pix, parcela.valor)
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/obter_payload_parcela/<int:parcela_id>')
@login_required
def obter_payload_parcela(parcela_id):
    parcela = Parcela.query.get_or_404(parcela_id)
    if parcela.usuario_id != current_user.id:
        return "Acesso negado", 403
    chave_pix = "majenkyo@gmail.com"
    payload = gerar_payload_pix(chave_pix, parcela.valor)
    return payload, 200, {'Content-Type': 'text/plain'}

# --- Pagamento com Cartão (InfinitePay) ---
INFINITETAG = "victor-paula"

@app.route('/pagar_parcela_cartao/<int:parcela_id>')
@login_required
def pagar_parcela_cartao(parcela_id):
    parcela = Parcela.query.get_or_404(parcela_id)
    if parcela.usuario_id != current_user.id:
        flash('Acesso negado a esta parcela.', 'danger')
        return redirect(url_for('minhas_parcelas'))

    payload = {
        "handle": INFINITETAG,
        "redirect_url": url_for('pagamento_confirmado', _external=True),
        "webhook_url": url_for('webhook_infinitepay', _external=True),
        "order_nsu": str(parcela.id),
        "items": [
            {
                "quantity": 1,
                "price": int(parcela.valor * 100),
                "description": f"Parcela {parcela.numero} - {parcela.familiar.nome if parcela.familiar else current_user.nome}"
            }
        ],
        "shipping": {
            "name": "Cliente",
            "address": "Endereço do Evento",
            "city": "Sua Cidade",
            "state": "SP",
            "zip_code": "00000-000",
            "country": "BR"
        }
    }

    try:
        response = requests.post(
            'https://api.infinitepay.io/invoices/public/checkout/links',
            json=payload,
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status()
        data = response.json()
        payment_url = data.get('url')
        if payment_url:
            return redirect(payment_url)
        else:
            flash('Erro ao criar link de pagamento: resposta inválida.', 'danger')
    except requests.exceptions.RequestException as e:
        print(f"Erro na API InfinitePay: {e}")
        if e.response is not None:
            print(f"Detalhes: {e.response.text}")
        flash('Não foi possível conectar ao serviço de pagamento. Tente novamente.', 'danger')
    return redirect(url_for('minhas_parcelas'))

@app.route('/pagamento_confirmado')
@login_required
def pagamento_confirmado():
    flash('Seu pagamento foi processado! Assim que confirmado, o status será atualizado.', 'info')
    return redirect(url_for('minhas_parcelas'))

@app.route('/webhook_infinitepay', methods=['POST'])
def webhook_infinitepay():
    data = request.get_json()
    if not data:
        return {"success": False, "message": "Invalid data"}, 400

    order_nsu = data.get('order_nsu')
    is_paid = data.get('paid', False)
    capture_method = data.get('capture_method')

    if is_paid and capture_method == 'credit_card':
        parcela = Parcela.query.get(int(order_nsu))
        if parcela and parcela.status != 'confirmado':
            parcela.status = 'confirmado'
            parcela.data_pagamento = date.today()
            mov = Movimentacao(
                descricao=f'Pagamento cartão - Parcela {parcela.numero} - {parcela.familiar.nome if parcela.familiar else parcela.usuario.nome}',
                valor=parcela.valor,
                tipo='entrada',
                data_mov=date.today(),
                comprovante_path=data.get('receipt_url')
            )
            db.session.add(mov)
            db.session.commit()
            assunto = f"Pagamento confirmado (cartão) - Parcela {parcela.numero}"
            mensagem = f"O pagamento via cartão da parcela {parcela.numero} (R$ {parcela.valor:.2f}) foi confirmado. O valor já foi adicionado ao caixa."
            enviar_email_notificacao(assunto, mensagem)
            return {"success": True, "message": "Pagamento confirmado"}, 200
        else:
            return {"success": False, "message": "Pedido não encontrado ou já confirmado"}, 400

    return {"success": True, "message": "Recebido, mas pagamento não aprovado."}, 200

# --- Área administrativa (apenas admin) ---
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
            descricao=f'Pagamento parcela {parcela.numero} - {parcela.familiar.nome if parcela.familiar else parcela.usuario.nome}',
            valor=parcela.valor,
            tipo='entrada',
            data_mov=date.today(),
            comprovante_path=parcela.comprovante_path
        )
        db.session.add(mov)
        db.session.commit()
        assunto = f"Pagamento confirmado (admin) - Parcela {parcela.numero}"
        mensagem = f"Você confirmou manualmente o pagamento da parcela {parcela.numero} de {parcela.familiar.nome if parcela.familiar else parcela.usuario.nome} (R$ {parcela.valor:.2f})."
        enviar_email_notificacao(assunto, mensagem)
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        inicializar_dados()
    app.run(debug=True)