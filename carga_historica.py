# carga_historica.py  -  CARGA DO HISTÓRICO (API Open-Meteo -> Supabase)

# O coletor normal só guarda o clima do MOMENTO, então o histórico começaria vazio e levaria dias para encher. Este arquivo resolve isso: a Open-Meteo também devolve dados hora a hora dos últimos dias, e aqui buscamos 7 dias de uma vez para as cidades já ficarem com gráficos completos.

# Pode ser usado de duas formas:
#   - rodando "python carga_historica.py" (carrega todas as cidades ativas);
#   - pelo app.py, que importa buscar_historico() ao cadastrar uma cidade nova.
#---------------------------------------------------------------------------------------------------------------------------------------------

from datetime import datetime

import requests

from servicos import supabase, invalidar_cache

URL_API = "https://api.open-meteo.com/v1/forecast"


def buscar_historico(latitude, longitude, dias=7):
    # Busca os dados hora a hora dos últimos `dias` dias para um ponto.
    params = {
        "latitude": latitude,
        "longitude": longitude,
        # "hourly" = série hora a hora (o coletor usa "current" = só o momento atual).
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "past_days": dias,       # quantos dias para trás
        "forecast_days": 1,      # a API exige pelo menos 1 dia de previsão
        "timezone": "America/Fortaleza",
    }
    resposta = requests.get(URL_API, params=params, timeout=20)
    resposta.raise_for_status()
    h = resposta.json()["hourly"]

    agora = datetime.now()
    linhas = []
    # A API devolve listas paralelas (uma de horários, uma de temperaturas...). O índice i liga o horário à temperatura, umidade, chuva e vento da mesma hora.
    for i, momento in enumerate(h["time"]):
        if datetime.fromisoformat(momento) > agora:
            continue  # ignora horas futuras: elas são PREVISÃO, não leitura real
        linhas.append({
            "temperatura": h["temperature_2m"][i],
            "umidade": h["relative_humidity_2m"][i],
            "chuva": h["precipitation"][i],
            "vento": h["wind_speed_10m"][i],
            "data_hora": momento,
        })
    return linhas


# Quando o arquivo é executado direto, carrega o histórico de todas as cidades ativas.
if __name__ == "__main__":
    cidades = supabase.table("cidades").select("*").eq("ativa", True).execute().data
    for cidade in cidades:
        linhas = buscar_historico(cidade["latitude"], cidade["longitude"])
        registros = [{"cidade_id": cidade["id"], **linha} for linha in linhas]
        # UPSERT: se o script rodar de novo, atualiza as leituras que já existem em vez de duplicá-las (regra única em cidade_id + data_hora).
        supabase.table("leituras").upsert(
            registros, on_conflict="cidade_id,data_hora"
        ).execute()
        print(f"OK  {cidade['nome']}: {len(registros)} registros")
    # Dados novos no banco: o cache do Redis estaria velho, então é apagado.
    invalidar_cache()
    print("\nHistórico carregado e cache limpo.")