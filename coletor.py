# coletor.py  -  COLETA DE DADOS (API Open-Meteo -> Supabase)

# Este arquivo é a "porta de entrada" dos dados do sistema. Ele:
#   1. lê do Supabase a lista de cidades que devem ser monitoradas;
#   2. consulta a API gratuita Open-Meteo para saber o clima atual de cada uma;
#   3. grava cada leitura na tabela "leituras" do Supabase;
#   4. atualiza o ranking no Redis e apaga o cache antigo.

# Pode ser usado de três formas:
#   - pelo botão "Coletar dados agora" do app.py (que chama coletar_tudo());
#   - rodando "python coletor.py" no terminal (uma coleta só);
#   - ou automaticamente, de 15 em 15 minutos, via coletor_loop.py.
#---------------------------------------------------------------------------------------------------------------------------------------------

import os
import requests
from dotenv import load_dotenv
from supabase import create_client

from servicos import invalidar_cache, atualizar_rankings

# Lê o .env (chaves ficam fora do código).
load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Endereço da API de previsão do tempo. É gratuita e não exige chave de acesso.
URL_API = "https://api.open-meteo.com/v1/forecast"


def listar_cidades_ativas():
    # SELECT * FROM cidades WHERE ativa = true.
    # As cidades vêm do BANCO, e não escritas no código: para monitorar uma cidade nova basta cadastrá-la (pela tela), sem mexer neste arquivo.
    resposta = supabase.table("cidades").select("*").eq("ativa", True).execute()
    return resposta.data


def buscar_clima(latitude, longitude):
    # Pede à API o clima atual de um ponto (latitude/longitude).
    params = {
        "latitude": latitude,
        "longitude": longitude,
        # Variáveis pedidas: temperatura, umidade, chuva e vento.
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        # Fuso horário de Fortaleza (UTC-3), para o horário vir no horário local.
        "timezone": "America/Fortaleza",
    }
    # timeout=10: se a API não responder em 10 segundos, desiste com erro.
    resposta = requests.get(URL_API, params=params, timeout=10)
    # Se a API devolveu erro (ex.: 500), levanta uma exceção em vez de seguir com dado inválido.
    resposta.raise_for_status()
    atual = resposta.json()["current"]
    # Traduz os nomes da API para os nomes das colunas do nosso banco.
    return {
        "temperatura": atual["temperature_2m"],
        "umidade": atual["relative_humidity_2m"],
        "chuva": atual["precipitation"],
        "vento": atual["wind_speed_10m"],
        "data_hora": atual["time"],
    }


def salvar_leitura(cidade_id, leitura):
    # UPSERT = insere ou atualiza. A tabela "leituras" tem a regra de que a combinação (cidade_id, data_hora) é ÚNICA. Então, se a API ainda devolver o mesmo horário da coleta anterior, a leitura é atualizada em vez de duplicada. Rodar o coletor várias vezes seguidas é seguro.
    registro = {"cidade_id": cidade_id, **leitura}
    supabase.table("leituras").upsert(
        registro, on_conflict="cidade_id,data_hora"
    ).execute()


def coletar_tudo():
    # Faz uma rodada completa de coleta para todas as cidades ativas.
    coletadas = []
    for cidade in listar_cidades_ativas():
        # try/except: se UMA cidade falhar (ex.: API fora do ar), o erro é mostrado e a coleta continua com as outras.
        try:
            leitura = buscar_clima(cidade["latitude"], cidade["longitude"])
            salvar_leitura(cidade["id"], leitura)
            coletadas.append({"cidade": cidade["nome"], **leitura})
            print(f"OK  {cidade['nome']}: {leitura['temperatura']}°C às {leitura['data_hora']}")
        except Exception as erro:
            print(f"ERRO em {cidade['nome']}: {erro}")

    # Integração com o Redis, logo após gravar no Supabase:
    #  1) atualiza os rankings (sorted sets) com as temperaturas novas;
    #  2) Invalida o cache, porque o painel guardado no Redis agora está velho.
    # A próxima consulta vai ao Supabase, pega o dado novo e recria o cache.
    atualizar_rankings(coletadas)
    invalidar_cache()
    print(f"\n{len(coletadas)} leitura(s) salva(s). Ranking atualizado e cache limpo.")


# Este bloco só roda quando o arquivo é executado diretamente (python coletor.py). Quando o app.py importa este arquivo, ele não é executado.
if __name__ == "__main__":
    coletar_tudo()