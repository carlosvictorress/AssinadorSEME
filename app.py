import os
import fitz  # PyMuPDF
import json
import uuid
import io
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from PIL import Image

# 1. Configuração da Aplicação
app = Flask(__name__)

# CONFIGURAÇÃO DE AMBIENTE: Vercel vs Local
# No Vercel, o sistema de arquivos é somente leitura, exceto a pasta /tmp
if os.environ.get('VERCEL'):
    UPLOAD_FOLDER = '/tmp'
else:
    UPLOAD_FOLDER = 'uploads'
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 2. Rota Principal
@app.route('/')
def index():
    # O Flask buscará index.html dentro da pasta 'templates'
    return render_template('index.html')

# 3. Rota de Upload Temporário
@app.route('/upload_temp', methods=['POST'])
def upload_temp():
    if 'pdf' not in request.files or 'assinatura' not in request.files:
        return jsonify({'error': 'Arquivos ausentes'}), 400
    
    pdf = request.files['pdf']
    assinatura = request.files['assinatura']
    
    # Gerar IDs únicos para evitar conflitos de nomes
    pdf_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    
    pdf_filename = f"{pdf_id}_{secure_filename(pdf.filename)}"
    img_filename = f"{img_id}_{secure_filename(assinatura.filename)}"
    
    # Salva no diretório configurado (/tmp no Vercel)
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
    img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_filename)
    
    pdf.save(pdf_path)
    assinatura.save(img_path)
    
    # Abre o PDF para contar as páginas
    doc = fitz.open(pdf_path)
    total_paginas = len(doc)
    doc.close()
    
    return jsonify({
        'pdf_name': pdf_filename,
        'img_name': img_filename,
        'total_paginas': total_paginas
    })

# 4. Rota para gerar Preview das páginas (necessário para o editor)
@app.route('/preview/<filename>/<int:pagina>')
def preview(filename, pagina):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        doc = fitz.open(filepath)
        page = doc.load_page(pagina - 1)
        # Gera uma imagem da página com zoom moderado (1.2x)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)) 
        output = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(output), mimetype='image/png')
    except Exception as e:
        return str(e), 500

# 5. Rota Final: Processa a assinatura e gera o download
@app.route('/assinar', methods=['POST'])
def assinar():
    dados = request.form
    pdf_name = dados.get('pdf_name')
    img_name = dados.get('img_name')
    escala_assinatura = float(dados.get('escala', 80))
    mapa_assinaturas = json.loads(dados.get('mapa_assinaturas'))
    
    # Nome personalizado definido pelo usuário no HTML
    nome_usuario = dados.get('nome_final', 'documento_assinado').strip()
    if not nome_usuario: nome_usuario = "documento_assinado"
    nome_download = nome_usuario if nome_usuario.lower().endswith('.pdf') else f"{nome_usuario}.pdf"

    path_pdf = os.path.join(app.config['UPLOAD_FOLDER'], pdf_name)
    path_img_original = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
    path_img_otimizada = os.path.join(app.config['UPLOAD_FOLDER'], f"mini_{img_name}")
    
    # --- REDIMENSIONAR IMAGEM (Mantém o PDF leve < 3MB) ---
    try:
        with Image.open(path_img_original) as img:
            img = img.convert("RGBA")
            largura_max = 400 
            w_percent = (largura_max / float(img.size[0]))
            h_size = int((float(img.size[1]) * float(w_percent)))
            img = img.resize((largura_max, h_size), Image.Resampling.LANCZOS)
            img.save(path_img_otimizada, "PNG", optimize=True)
    except:
        path_img_otimizada = path_img_original

    path_saida = os.path.join(app.config['UPLOAD_FOLDER'], f"final_{str(uuid.uuid4())[:8]}.pdf")

    doc = fitz.open(path_pdf)
    
    # Itera sobre as páginas que receberam assinatura
    for num_pag_str, coords in mapa_assinaturas.items():
        try:
            page = doc[int(num_pag_str) - 1]
            pos_x = float(coords['x']) * page.rect.width
            pos_y = float(coords['y']) * page.rect.height
            
            # Proporção da assinatura
            largura_final = escala_assinatura
            with Image.open(path_img_otimizada) as temp_img:
                proporcao = temp_img.height / temp_img.width
            altura_final = largura_final * proporcao
            
            # Retângulo de inserção
            rect = fitz.Rect(
                pos_x - (largura_final/2), pos_y - (altura_final/2),
                pos_x + (largura_final/2), pos_y + (altura_final/2)
            )
            page.insert_image(rect, filename=path_img_otimizada, overlay=True)
        except: continue

    # --- SALVAMENTO OTIMIZADO (Correção do erro de Linearização) ---
    doc.save(
        path_saida, 
        garbage=4,             # Remove lixo e duplicatas
        deflate=True,          # Comprime fluxos de dados
        deflate_images=True,   # Comprime imagens internas
        clean=True             # Organiza a estrutura
    )
    doc.close()

    return send_file(path_saida, as_attachment=True, download_name=nome_download)

# Necessário para o Vercel identificar o objeto app
app = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)