from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin  # <--- IMPORTANTE

db = SQLAlchemy()

class Usuario(UserMixin, db.Model):  # <--- HERDA DE UserMixin
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(200), nullable=False)
    telefone = db.Column(db.String(20))
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    parcelas = db.relationship('Parcela', backref='usuario', lazy=True)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def verificar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

# O restante das classes (DiaEvento, Refeicao, Movimentacao, ItemCompra, Parcela, Foto) continua igual

class DiaEvento(db.Model):
    __tablename__ = 'dias_evento'
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False, unique=True)
    refeicoes = db.relationship('Refeicao', backref='dia', cascade='all, delete-orphan')

class Refeicao(db.Model):
    __tablename__ = 'refeicoes'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(20), nullable=False)
    cardapio = db.Column(db.Text, nullable=False)
    dia_id = db.Column(db.Integer, db.ForeignKey('dias_evento.id'), nullable=False)

class Movimentacao(db.Model):
    __tablename__ = 'movimentacoes'
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    tipo = db.Column(db.String(10), nullable=False)
    data_mov = db.Column(db.Date, nullable=False, default=datetime.today)
    comprovante_path = db.Column(db.String(300), nullable=True)

class ItemCompra(db.Model):
    __tablename__ = 'itens_compra'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    quantidade = db.Column(db.String(50), nullable=True)
    comprado = db.Column(db.Boolean, default=False)
    categoria = db.Column(db.String(50), nullable=True)

class Parcela(db.Model):
    __tablename__ = 'parcelas'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='pendente')
    comprovante_path = db.Column(db.String(300))
    data_pagamento = db.Column(db.Date)
    observacao = db.Column(db.Text)

class Foto(db.Model):
    __tablename__ = 'fotos'
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100))
    descricao = db.Column(db.Text)
    imagem_path = db.Column(db.String(300), nullable=False)
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)