# servicos.py  -  CAMADA DE DADOS DO PROJETO (Supabase + Redis)

# Este arquivo concentra toda a comunicação com os dois bancos. As telas (app.py) e o coletor (coletor.py) nunca falam direto com o Redis: eles chamam as funções que estão aqui.

# Papel de cada tecnologia
#   Supabase (PostgreSQL) -> guarda os dados PERMANENTES: as cidades e todas as leituras de clima (temperatura, umidade...).
#   Redis                 -> guarda dados TEMPORÁRIOS na memória, que precisam ser lidos muito rápido e podem ser refeitos a
#                            qualquer momento a partir do Supabase.
# Onde o Redis é usado:
#   1. Cache do painel     -> chave "painel:v2" (texto JSON), expira em 10 min
#   2. Cache do histórico  -> chaves "historico:<periodo>", expiram em 5 min
#   3. Ranking de cidades  -> sorted sets "ranking:temperatura" e "ranking:chuva"
#   4. Contadores do cache -> chaves "stats:cache_hits" e "stats:cache_miss" (comando INCR, que soma 1 de forma atômica)

# Problema que o Redis resolve?
# Montar o painel exige consultar o banco (Supabase) na internet, o que é lento (alguns segundos) e repetitivo: os dados só mudam a cada coleta. Com o Redis, a primeira consulta vai ao Supabase e o resultado fica guardado; as próximas saem da memória do Redis, muito mais rápido.

# Padrão-Usado: "CACHE-ASIDE" (cache ao lado)
#   1. A aplicação pergunta primeiro ao Redis.
#   2. Se achou (HIT)  -> devolve na hora.
#   3. Se não achou (MISS) -> busca no Supabase, guarda no Redis com prazo de validade (TTL) e devolve.
#   4. Quando chega dado novo (coleta) ou uma cidade muda, o cache é apagado (invalidação) para o painel não mostrar informação velha.
#---------------------------------------------------------------------------------------------------------------------------------------------

import os
import json
import time
from datetime import datetime, timedelta

import redis
from dotenv import load_dotenv
from supabase import create_client

# Lê o arquivo .env (URL e chaves). Assim nenhuma senha fica escrita no código nem vai para o GitHub.
load_dotenv()

# Conexão com o Supabase. Usamos a chave "secret", que ignora o RLS (a proteção de linhas do Supabase) e por isso consegue ler e gravar nas tabelas.
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Conexão com o Redis (Redis Cloud). decode_responses=True faz o Redis devolver texto normal em vez de bytes, o que simplifica o código.
r = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    username="default",
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
)

# Textos que aparecem na tela para mostrar de onde veio o dado.
ORIGEM_REDIS = "Redis (cache)"
ORIGEM_BANCO = "Supabase (banco)"

# O "v2" no nome da chave serve para ignorar caches antigos de versões anteriores do projeto (que tinham outro formato de dados).
CHAVE_PAINEL = "painel:v2"
TTL_PAINEL = 600      # TTL = "time to live": depois de 10 minutos o Redis apaga a chave sozinho
TTL_HISTORICO = 300   # o histórico muda menos, então basta 5 minutos

# Períodos do histórico: nome mostrado na tela -> quantidade de horas.
PERIODOS = {"Últimas 24 horas": 24, "Últimos 7 dias": 168}


# Painel
def _buscar_painel_supabase():
    """Monta os dados do painel direto do banco (é o caminho LENTO, usado no MISS).

    Faz só 2 consultas: as cidades ativas e a última leitura de cada uma.
    A "última leitura" vem da view `ultimas_leituras`, criada no Supabase com
    DISTINCT ON (cidade_id): o próprio banco escolhe a leitura mais recente de
    cada cidade, em vez de fazermos uma consulta por cidade.
    """
    cidades = (
        supabase.table("cidades").select("*").eq("ativa", True).order("nome").execute().data
    )
    ultimas = supabase.table("ultimas_leituras").select("*").execute().data

    # Dicionário {id da cidade: leitura} para juntar as duas listas rapidamente.
    por_cidade = {u["cidade_id"]: u for u in ultimas}

    painel = []
    for c in cidades:
        l = por_cidade.get(c["id"])
        # Cidade recém-cadastrada, sem leitura ainda: fica fora do painel.
        if not l or l["temperatura"] is None:
            continue
        painel.append({
            "id": c["id"],
            "cidade": c["nome"],
            "limite": c["limite_alerta_temp"],
            "lat": c["latitude"],
            "lon": c["longitude"],
            "temperatura": l["temperatura"],
            # A API às vezes devolve valor vazio (None); trocamos por 0 para a tela não quebrar.
            "umidade": l["umidade"] if l["umidade"] is not None else 0,
            "chuva": l["chuva"] if l["chuva"] is not None else 0,
            "vento": l["vento"] if l["vento"] is not None else 0,
            "data_hora": l["data_hora"],
        })
    return painel


def obter_painel():
    """FUNÇÃO CENTRAL DO CACHE. Retorna (dados, origem, tempo_em_ms).

    Segue o padrão cache-aside: tenta o Redis antes do Supabase.
    O tempo medido é o que aparece na tela ("Fonte dos dados ... ms") e
    que o Redis acelera a consulta.
    """
    inicio = time.perf_counter()
    cache = r.get(CHAVE_PAINEL)  # GET: pergunta ao Redis se a chave existe
    if cache:
        # HIT: o Redis tinha o dado. O valor está guardado como texto JSON.
        dados, origem = json.loads(cache), ORIGEM_REDIS
    else:
        # MISS: não havia cache (expirou ou foi apagado). Busca no Supabase
        dados, origem = _buscar_painel_supabase(), ORIGEM_BANCO
        # e guarda no Redis (SET) com expiração (ex=segundos) para as próximas consultas.
        r.set(CHAVE_PAINEL, json.dumps(dados), ex=TTL_PAINEL)
    return dados, origem, (time.perf_counter() - inicio) * 1000


# Histórico
def _buscar_historico_supabase(horas):
    """Busca no banco as leituras das últimas `horas` horas (caminho LENTO).

    O Supabase devolve no máximo 1000 linhas por consulta. Como o histórico
    de 7 dias tem mais que isso, buscamos em PÁGINAS de 1000 linhas
    (.range) até acabar. O limite de 10 páginas evita um laço infinito.
    """
    limite = (datetime.now() - timedelta(hours=horas)).isoformat()
    linhas, pagina, tamanho = [], 0, 1000
    while True:
        resposta = (
            supabase.table("leituras")
            # "cidades(nome)" faz um JOIN: traz o nome da cidade junto de cada leitura.
            .select("temperatura, umidade, chuva, vento, data_hora, cidades(nome)")
            .gte("data_hora", limite)  # gte = "maior ou igual": só leituras recentes
            # Dois critérios de ordenação para a paginação ser estável. Sem o
            # "id", leituras com o mesmo horário poderiam repetir ou sumir entre páginas.
            .order("data_hora")
            .order("id")
            .range(pagina * tamanho, (pagina + 1) * tamanho - 1)
            .execute()
            .data
        )
        linhas.extend(resposta)
        # Se veio menos de uma página cheia, não há mais dados.
        if len(resposta) < tamanho or pagina >= 9:
            break
        pagina += 1

    # Simplifica cada linha: troca o objeto aninhado "cidades" pelo nome direto.
    return [
        {
            "cidade": l["cidades"]["nome"],
            "temperatura": l["temperatura"],
            "umidade": l["umidade"],
            "chuva": l["chuva"],
            "vento": l["vento"],
            "data_hora": l["data_hora"],
        }
        for l in linhas
    ]


def obter_historico(periodo):
    """Mesmo padrão cache-aside do painel, agora para o histórico.

    Retorna (linhas, origem, tempo_em_ms). A chave inclui o período
    ("historico:Últimos 7 dias"), então cada período tem o seu próprio cache.
    """
    inicio = time.perf_counter()
    chave = f"historico:{periodo}"
    cache = r.get(chave)
    if cache:
        dados, origem = json.loads(cache), ORIGEM_REDIS
    else:
        dados, origem = _buscar_historico_supabase(PERIODOS[periodo]), ORIGEM_BANCO
        r.set(chave, json.dumps(dados), ex=TTL_HISTORICO)
    return dados, origem, (time.perf_counter() - inicio) * 1000


#  Cache
def invalidar_cache():
    """INVALIDAÇÃO: apaga o cache do painel e todos os de histórico.

    É chamada sempre que os dados mudam (nova coleta, cidade criada, editada
    ou excluída). Sem isso, o painel mostraria dados velhos até o TTL acabar.
    scan_iter percorre as chaves "historico:*" sem travar o Redis.
    """
    chaves = [CHAVE_PAINEL, *r.scan_iter("historico:*")]
    r.delete(*chaves)


def registrar_consulta(origem=None):
    """Conta cada consulta como HIT (veio do Redis) ou MISS (veio do banco).

    Usa INCR, que soma 1 ao contador de forma atômica (seguro mesmo com
    vários acessos ao mesmo tempo). Usa também um PIPELINE: agrupa o INCR e as
    duas leituras dos contadores em UMA única viagem de rede ao Redis. Como
    cada viagem custa uns 300 ms (o servidor fica nos EUA), isso economiza
    tempo. Os números alimentam a barra lateral ("Do cache" e "Do banco").
    """
    pipe = r.pipeline()
    if origem == ORIGEM_REDIS:
        pipe.incr("stats:cache_hits")
    elif origem == ORIGEM_BANCO:
        pipe.incr("stats:cache_miss")
    pipe.get("stats:cache_hits")
    pipe.get("stats:cache_miss")
    resultado = pipe.execute()
    # Os dois últimos resultados são os valores dos contadores (None se ainda não existem).
    return int(resultado[-2] or 0), int(resultado[-1] or 0)


def estatisticas_cache():
    # Só lê os contadores, sem somar nada. Mantida por compatibilidade com o teste antigo.
    return registrar_consulta(None)


# RANKING
def atualizar_rankings(painel):
    """Grava o ranking de temperatura e de chuva em SORTED SETS do Redis.

    Um sorted set é uma coleção de itens únicos, cada um com uma pontuação
    (score), que o Redis mantém SEMPRE ordenada pela pontuação. Aqui o item é
    o nome da cidade e a pontuação é a temperatura (ou a chuva). ZADD insere
    ou atualiza a pontuação: se a cidade já existe, só troca o valor.
    A vantagem: o ranking nunca precisa ser reordenado no código.
    """
    for item in painel:
        r.zadd("ranking:temperatura", {item["cidade"]: item["temperatura"]})
        r.zadd("ranking:chuva", {item["cidade"]: item["chuva"]})


def top_ranking(chave, n=10):
    # ZREVRANGE devolve do MAIOR para o MENOR score (mais quente primeiro).
    # withscores=True traz também o valor: [("Teresina", 36.1), ...].
    return r.zrevrange(chave, 0, n - 1, withscores=True)


def remover_dos_rankings(nome):
    # ZREM tira a cidade do ranking (usado ao excluir, renomear ou desativar).
    r.zrem("ranking:temperatura", nome)
    r.zrem("ranking:chuva", nome)


#  CADASTRO DE CIDADES
def validar_cidade(nome, latitude, longitude):
    """Valida os dados antes de gravar. Devolve uma mensagem de erro ou None.

    A TRAVA CONTRA COORDENADAS ZERADAS fica aqui: latitude 0 e longitude 0
    é um ponto no oceano (Golfo da Guiné) e gerava leituras erradas.
    """
    if not nome or not nome.strip():
        return "Informe o nome da cidade."
    if latitude is None or longitude is None:
        return "Informe a latitude e a longitude."
    if latitude == 0 and longitude == 0:
        return ("Latitude e longitude zeradas apontam para o meio do oceano. "
                "Informe as coordenadas reais da cidade.")
    # Limites reais do globo terrestre.
    if not -90 <= latitude <= 90:
        return "A latitude precisa estar entre -90 e 90."
    if not -180 <= longitude <= 180:
        return "A longitude precisa estar entre -180 e 180."
    return None


def listar_cidades():
    # SELECT * FROM cidades ORDER BY nome. Sem cache de propósito: a tela de cadastro precisa sempre da lista mais atual.
    return supabase.table("cidades").select("*").order("nome").execute().data


def adicionar_cidade(nome, latitude, longitude, limite):
    # Valida antes de gravar. Se houver erro, levanta uma exceção que a tela mostra ao usuário.
    erro = validar_cidade(nome, latitude, longitude)
    if erro:
        raise ValueError(erro)
    # INSERT no Supabase. O nome é "unique" no banco, então nomes repetidos são recusados lá.
    resposta = supabase.table("cidades").insert({
        "nome": nome.strip(),
        "latitude": latitude,
        "longitude": longitude,
        "limite_alerta_temp": limite,
    }).execute()
    # Devolve a linha criada (com o id gerado pelo banco).
    return resposta.data[0]


def atualizar_cidade(cidade_id, nome_antigo, nome, latitude, longitude, limite, ativa):
    erro = validar_cidade(nome, latitude, longitude)
    if erro:
        raise ValueError(erro)
    # UPDATE no Supabase, identificando a cidade pelo id (que não muda).
    supabase.table("cidades").update({
        "nome": nome.strip(),
        "latitude": latitude,
        "longitude": longitude,
        "limite_alerta_temp": limite,
        "ativa": ativa,
    }).eq("id", cidade_id).execute()
    # O ranking usa o NOME como identificador. Se o nome mudou (ou a cidade foi
    # desativada), o nome antigo precisa sair do ranking do Redis.
    if nome.strip() != nome_antigo or not ativa:
        remover_dos_rankings(nome_antigo)
    invalidar_cache()


def apagar_leituras(cidade_id):
    # Usado quando a localização muda: as leituras antigas eram de outro lugar.
    supabase.table("leituras").delete().eq("cidade_id", cidade_id).execute()


def excluir_cidade(cidade_id, nome):
    # As leituras da cidade somem junto, por causa do "on delete cascade" da tabela.
    supabase.table("cidades").delete().eq("id", cidade_id).execute()
    # Mantém o Redis coerente com o banco: tira do ranking e limpa o cache.
    remover_dos_rankings(nome)
    invalidar_cache()