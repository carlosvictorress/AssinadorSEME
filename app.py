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
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 2. Rota Principal
@app.route('/')
def index():
    return render_template('index.html')

# 3. Rota de Upload Temporário (PDF e Assinatura)
@app.route('/upload_temp', methods=['POST'])
def upload_temp():
    if 'pdf' not in request.files or 'assinatura' not in request.files:
        return jsonify({'error': 'Arquivos ausentes'}), 400
    
    pdf = request.files['pdf']
    assinatura = request.files['assinatura']
    
    # Gerar nomes únicos para evitar conflitos de permissão no Windows
    pdf_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    
    pdf_filename = f"{pdf_id}_{secure_filename(pdf.filename)}"
    img_filename = f"{img_id}_{secure_filename(assinatura.filename)}"
    
    pdf.save(os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename))
    assinatura.save(os.path.join(app.config['UPLOAD_FOLDER'], img_filename))
    
    # Abrir para contar páginas
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
        # Matrix 1.2 reduz um pouco a qualidade do preview para carregar mais rápido
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)) 
        output = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(output), mimetype='image/png')
    except Exception as e:
        return str(e), 500

# 5. Rota de Processamento Final (Assinatura + Compressão)
@app.route('/assinar', methods=['POST'])
def assinar():
    """
    Processa a assinatura do PDF, redimensiona a imagem para poupar espaço
    e permite nomear o ficheiro de saída.
    """
    dados = request.form
    pdf_name = dados.get('pdf_name')
    img_name = dados.get('img_name')
    
    # Captura a escala (tamanho visual) definida no slider do HTML
    escala_assinatura = float(dados.get('escala', 80))
    
    # Captura o mapa de coordenadas de cada página
    mapa_assinaturas = json.loads(dados.get('mapa_assinaturas'))
    
    # Captura o nome personalizado do ficheiro (ou usa um padrão)
    nome_usuario = dados.get('nome_final', 'documento_assinado').strip()
    if not nome_usuario:
        nome_usuario = "documento_assinado"
    
    # Garante que o nome termine com .pdf
    if not nome_usuario.lower().endswith('.pdf'):
        nome_download = f"{nome_usuario}.pdf"
    else:
        nome_download = nome_usuario

    # Caminhos dos ficheiros
    path_pdf = os.path.join(app.config['UPLOAD_FOLDER'], pdf_name)
    path_img_original = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
    path_img_otimizada = os.path.join(app.config['UPLOAD_FOLDER'], f"mini_{img_name}")
    
    # 1. REDIMENSIONAR A IMAGEM (Garante ficheiros pequenos < 3MB)
    try:
        with Image.open(path_img_original) as img:
            img = img.convert("RGBA")
            # Reduz a largura para 400px (ideal para assinaturas, ocupa pouco espaço)
            largura_max = 400 
            w_percent = (largura_max / float(img.size[0]))
            h_size = int((float(img.size[1]) * float(w_percent)))
            img = img.resize((largura_max, h_size), Image.Resampling.LANCZOS)
            img.save(path_img_otimizada, "PNG", optimize=True)
    except Exception as e:
        print(f"Erro na otimização da imagem: {e}")
        path_img_otimizada = path_img_original

    # Nome único para o ficheiro temporário no servidor
    id_unico = str(uuid.uuid4())[:8]
    path_saida = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_final_{id_unico}.pdf")

    # 2. INSERIR ASSINATURAS NO PDF
    doc = fitz.open(path_pdf)
    
    for num_pag_str, coords in mapa_assinaturas.items():
        try:
            # Carrega a página (índice começa em 0)
            page = doc[int(num_pag_str) - 1]
            
            # Converte coordenadas relativas (clique) em pontos do PDF
            pos_x = float(coords['x']) * page.rect.width
            pos_y = float(coords['y']) * page.rect.height
            
            # Calcula altura proporcional para não distorcer a assinatura
            largura_final = escala_assinatura
            with Image.open(path_img_otimizada) as temp_img:
                proporcao = temp_img.height / temp_img.width
            altura_final = largura_final * proporcao
            
            # Define o retângulo onde a imagem será inserida (centralizado no clique)
            rect = fitz.Rect(
                pos_x - (largura_final/2), pos_y - (altura_final/2),
                pos_x + (largura_final/2), pos_y + (altura_final/2)
            )
            
            # Insere a imagem
            page.insert_image(rect, filename=path_img_otimizada, overlay=True)
        except Exception as e:
            print(f"Erro ao processar página {num_pag_str}: {e}")
            continue

    # 3. SALVAMENTO COM COMPRESSÃO MÁXIMA
    doc.save(
        path_saida, 
        garbage=4,             # Nível máximo de limpeza de dados inúteis
        deflate=True,          # Comprime o conteúdo interno
        deflate_images=True,   # Comprime as imagens inseridas
        clean=True             # Organiza a estrutura interna do PDF
    )
    doc.close()

    # Envia o ficheiro para o utilizador com o nome escolhido
    return send_file(
        path_saida, 
        as_attachment=True, 
        download_name=nome_download
    )

if __name__ == "__main__":
    # Use host='0.0.0.0' para garantir que o ngrok consiga encaminhar o tráfego
    app.run(debug=True, host='0.0.0.0', port=5000)