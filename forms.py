from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, FloatField, SelectField, DateField, BooleanField, TextAreaField, PasswordField
from wtforms.validators import DataRequired, Optional, Email

class MovimentacaoForm(FlaskForm):
    descricao = StringField('Descrição', validators=[DataRequired()])
    valor = FloatField('Valor (R$)', validators=[DataRequired()])
    tipo = SelectField('Tipo', choices=[('entrada', 'Entrada'), ('saida', 'Saída')], validators=[DataRequired()])
    data_mov = DateField('Data', validators=[DataRequired()])
    comprovante = FileField('Comprovante', validators=[Optional(), FileAllowed(['jpg','png','jpeg','pdf'], 'Apenas imagens ou PDF')])

class ItemCompraForm(FlaskForm):
    nome = StringField('Item', validators=[DataRequired()])
    quantidade = StringField('Quantidade')
    categoria = StringField('Categoria')
    comprado = BooleanField('Já comprado?')

class RefeicaoForm(FlaskForm):
    cardapio = TextAreaField('Cardápio', validators=[DataRequired()])

class LoginForm(FlaskForm):
    senha = PasswordField('Senha administrativa', validators=[DataRequired()])

class PagamentoForm(FlaskForm):
    comprovante = FileField('Comprovante de pagamento', validators=[FileAllowed(['jpg','png','jpeg','pdf'], 'Apenas imagens ou PDF')])
    observacao = TextAreaField('Observação')

class FotoForm(FlaskForm):
    titulo = StringField('Título')
    descricao = TextAreaField('Descrição')
    imagem = FileField('Imagem', validators=[DataRequired(), FileAllowed(['jpg','png','jpeg'], 'Apenas imagens')])