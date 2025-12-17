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

# Configuração para o Vercel (usa /tmp pois o sistema de arquivos é somente leitura)
UPLOAD_FOLDER = '/tmp' if os.environ.get('VERCEL') else 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 2. Rota Principal
@app.route('/')
def index():
    # O Flask buscará dentro da pasta 'templates'
    return render_template('index.html')

# 3. Rota de Upload Temporário
@app.route('/upload_temp', methods=['POST'])
def upload_temp():
    if 'pdf' not in request.files or 'assinatura' not in request.files:
        return jsonify({'error': 'Arquivos ausentes'}), 400
    
    pdf = request.files['pdf']
    assinatura = request.files['assinatura']
    
    pdf_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    
    pdf_filename = f"{pdf_id}_{secure_filename(pdf.filename)}"
    img_filename = f"{img_id}_{secure_filename(assinatura.filename)}"
    
    pdf.save(os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename))
    assinatura.save(os.path.join(app.config['UPLOAD_FOLDER'], img_filename))
    
    doc = fitz.open(os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename))
    total_paginas = len(doc)
    doc.close()
    
    return jsonify({
        'pdf_name': pdf_filename,
        'img_name': img_filename,
        'total_paginas': total_paginas
    })

# 4. Rota para gerar Preview das páginas
@app.route('/preview/<filename>/<int:pagina>')
def preview(filename, pagina):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        doc = fitz.open(filepath)
        page = doc.load_page(pagina - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)) 
        output = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(output), mimetype='image/png')
    except Exception as e:
        return str(e), 500

# 5. Rota de Processamento Final (Assinatura + Compressão + Nome Personalizado)
@app.route('/assinar', methods=['POST'])
def assinar():
    dados = request.form
    pdf_name = dados.get('pdf_name')
    img_name = dados.get('img_name')
    escala_assinatura = float(dados.get('escala', 80))
    mapa_assinaturas = json.loads(dados.get('mapa_assinaturas'))
    
    # Nome personalizado do arquivo
    nome_usuario = dados.get('nome_final', 'documento_assinado').strip()
    if not nome_usuario: nome_usuario = "documento_assinado"
    nome_download = nome_usuario if nome_usuario.lower().endswith('.pdf') else f"{nome_usuario}.pdf"

    path_pdf = os.path.join(app.config['UPLOAD_FOLDER'], pdf_name)
    path_img_original = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
    path_img_otimizada = os.path.join(app.config['UPLOAD_FOLDER'], f"mini_{img_name}")
    
    # --- REDIMENSIONAR IMAGEM (Garante < 3MB) ---
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
    
    for num_pag_str, coords in mapa_assinaturas.items():
        try:
            page = doc[int(num_pag_str) - 1]
            pos_x = float(coords['x']) * page.rect.width
            pos_y = float(coords['y']) * page.rect.height
            
            largura_final = escala_assinatura
            with Image.open(path_img_otimizada) as temp_img:
                proporcao = temp_img.height / temp_img.width
            altura_final = largura_final * proporcao
            
            rect = fitz.Rect(
                pos_x - (largura_final/2), pos_y - (altura_final/2),
                pos_x + (largura_final/2), pos_y + (altura_final/2)
            )
            page.insert_image(rect, filename=path_img_otimizada, overlay=True)
        except: continue

    # --- SALVAMENTO OTIMIZADO (Sem Linearize para evitar erro) ---
    doc.save(
        path_saida, 
        garbage=4,             
        deflate=True,          
        deflate_images=True,   
        clean=True             
    )
    doc.close()

    return send_file(path_saida, as_attachment=True, download_name=nome_download)

# Necessário para o Vercel identificar a aplicação
app = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)