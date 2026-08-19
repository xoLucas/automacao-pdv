from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from geopy.geocoders import Nominatim
import googlemaps
import time
from supabase import create_client, Client
import io
from dotenv import load_dotenv
import os
from fastapi import Query

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# CONFIGURAÇÕES DE API
# ---------------------------------------------------------
# Carrega o arquivo específico que você criou
load_dotenv("api-pdv-tracker.env") 

URL_SUPABASE = os.getenv("Supabase_URL")
CHAVE_SUPABASE = os.getenv("Supabase_Key")
GOOGLE_MAPS_API_KEY = os.getenv("Maps_API_KEY")

supabase: Client = create_client(URL_SUPABASE, CHAVE_SUPABASE)
geolocator = Nominatim(user_agent="app_pdv_tracker_v3")
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

# =========================================================
# FUNÇÕES DE AUXÍLIO (GEOLOCALIZAÇÃO)
# =========================================================

def limpar_endereco(endereco):
    """ Remove ruídos como quebras de linha e fixa a região de busca """
    if not endereco: return ""
    # Remove o '\n' que aparece no final de alguns endereços na sua planilha
    endereco_limpo = str(endereco).replace('\n', ' ').strip()
    return endereco_limpo

def buscar_coordenadas(endereco_original):
    """ Tenta Nominatim (Grátis) -> Se falhar -> Google (Pago/Cota) """
    endereco_para_busca = limpar_endereco(endereco_original)
    
    # 1. TENTATIVA COM NOMINATIM
    try:
        location = geolocator.geocode(endereco_para_busca, timeout=10)
        if location:
            print(f"✅ Nominatim achou: {endereco_original}")
            return location.latitude, location.longitude
    except Exception as e:
        print(f"⚠️ Erro no Nominatim: {e}")

    # 2. TENTATIVA COM GOOGLE MAPS (Caso a primeira falhe)
    try:
        print(f"🔍 Nominatim falhou. Chamando Google para: {endereco_original}")
        result = gmaps.geocode(endereco_para_busca)
        if result:
            loc = result[0]['geometry']['location']
            return loc['lat'], loc['lng']
    except Exception as e:
        print(f"❌ Erro no Google Maps: {e}")

    return None, None

# =========================================================
# O MOTOR QUE RODA NOS BASTIDORES
# =========================================================
def processar_em_segundo_plano(df, grupo_id, coluna_endereco):
    # Remove linhas onde o endereço está vazio
    df = df.dropna(subset=[coluna_endereco])

    for index, row in df.iterrows():
        endereco_raw = str(row[coluna_endereco]).strip()

        if endereco_raw == "" or endereco_raw.lower() == "nan":
            continue

        # Busca inteligente (Cascata)
        lat, lon = buscar_coordenadas(endereco_raw)

        # Função segura para pegar as outras colunas
        def pegar_dado(nome_coluna, padrao):
            valor = row.get(nome_coluna, padrao)
            return padrao if pd.isna(valor) else str(valor).strip()

        pdv_data = {
            "numero_pdv": pegar_dado('PDV', 'N/A'),
            "nome": pegar_dado('Nome', 'Desconhecido'),
            "endereco": endereco_raw.replace('\n', ' '), 
            "cnpj": pegar_dado('CNPJ', 'Sem CNPJ'),
            "lat": lat,
            "lon": lon,
            "status": "pendente",
            "grupo_id": grupo_id,
            "pad_sub": pegar_dado('pad_sub', 'pad').lower()
        }

        try:
            supabase.table('pdvs').insert(pdv_data).execute()
        except Exception as e:
            print(f"Erro ao salvar no banco: {e}")

        # Pausa para respeitar os limites do Nominatim
        time.sleep(1.2)

# =========================================================
# A ROTA PRINCIPAL DO SITE
# =========================================================
@app.post("/processar-planilha/")
async def processar_planilha(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    grupo_id: str = Form(...)
):
    try:
        conteudo_arquivo = await file.read()
        
        if file.filename.lower().endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(conteudo_arquivo), sep=None, engine='python', encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(conteudo_arquivo), sep=None, engine='python', encoding='latin1')
        else:
            df = pd.read_excel(io.BytesIO(conteudo_arquivo))
            
        # Acha a coluna endereço (ignora maiúsculas/minúsculas)
        coluna_endereco = next((col for col in df.columns if str(col).lower().strip() == 'endereço'), None)
        
        if not coluna_endereco:
            return {"erro": "A planilha precisa ter uma coluna chamada 'Endereço'."}
            
        background_tasks.add_task(processar_em_segundo_plano, df, grupo_id, coluna_endereco)
        
        return {"mensagem": "Processamento iniciado! Os PDVs aparecerão no mapa conforme forem localizados."}
        
    except Exception as e:
        return {"erro": f"Erro ao ler arquivo: {str(e)}"}

#nova rota que busca coordenadas em um unico endereço
@app.get("/geolocalizar/")
async def geolocalizar_endereco(
    endereco: str = Query(..., description="Endereço para buscar as coordenadas")
):
    # Usamos a mesma função cascata (Nominatim -> Google) que você já criou!
    lat, lon = buscar_coordenadas(endereco)
    
    if lat and lon:
        return {"sucesso": True, "lat": lat, "lon": lon}
    else:
        return {"sucesso": False, "erro": "Não foi possível encontrar as coordenadas para este endereço."}