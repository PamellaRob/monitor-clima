# app.py  -  INTERFACE GRÁFICA (Streamlit) DO MONITOR DE CLIMA

# Este é o arquivo que o usuário vê. Executado com:  streamlit run app.py

# Como o Streamlit funciona? 
#   A cada clique em um botão ou campo, o Streamlit executa este arquivo INTEIRO de novo, de cima para baixo. Por isso o código é organizado em funções, uma por tela, e o menu lateral decide qual delas rodar.

# As 4 telas:
#   Painel   -> cartões com o clima atual de cada cidade + indicadores + mapa
#   Histórico-> gráficos da evolução do clima nos últimos dias
#   Ranking  -> cidades mais quentes e mais chuvosas (vem do Redis)
#   Cidades  -> cadastrar, editar e excluir cidades

# Onde cada tecnologia aparece neste arquivo
#   Redis    -> obter_painel() e obter_historico() (cache), top_ranking() (ranking) e registrar_consulta() (contadores da barra lateral).
#   Supabase -> listar/adicionar/atualizar/excluir cidades, e gravar leituras em carregar_dados_cidade().
#   Open-Meteo (API) -> buscar_clima() e buscar_historico(), usadas ao cadastrar cidade.
#---------------------------------------------------------------------------------------------------------------------------------------------

import html
import math
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from carga_historica import buscar_historico
from coletor import buscar_clima, coletar_tudo, salvar_leitura
from servicos import (
    PERIODOS,
    adicionar_cidade,
    apagar_leituras,
    atualizar_cidade,
    atualizar_rankings,
    excluir_cidade,
    invalidar_cache,
    listar_cidades,
    obter_historico,
    obter_painel,
    registrar_consulta,
    supabase,
    top_ranking,
    validar_cidade,
)

# Configuração da página. Precisa ser a primeira chamada do Streamlit
st.set_page_config(page_title="Monitor de Clima", page_icon="🌦️", layout="wide")

# Estilo
# Paleta de cores usada nos gráficos (azul, laranja, verde-água, rosa...)
CORES = ["#5B8DEF", "#FF9F5A", "#3CC8A6", "#F78FB3", "#9B8AFB", "#F4C542"]
# Escala de cores da temperatura: azul (frio) -> verde -> laranja (quente).
ESCALA_TEMP = ["#6C9BFF", "#7FD8BE", "#FFC27A", "#FF8A4C"]
ESCALA_CHUVA = ["#D6E4FF", "#5B8DEF"]

# Nome mostrado na tela -> nome real da coluna no banco
VARIAVEIS = {
    "Temperatura (°C)": "temperatura",
    "Umidade do ar (%)": "umidade",
    "Chuva (mm)": "chuva",
    "Vento (km/h)": "vento",
}

# Listas para escrever a data em português ("Sexta-feira, 23 de outubro de 2026").
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]

# CSS personalizado: dá o visual de cartões arredondados com sombra suave
# O Streamlit não tem cartões coloridos prontos, então foi desenhado em HTML/CSS
st.markdown(
    """
<style>
.block-container {padding-top: 2.2rem; max-width: 1250px;}
h1, h2, h3, h4 {color: #2B3674;}
.logo {font-size: 1.35rem; font-weight: 700; color: #4C7DF0; margin-bottom: .6rem;}
.saudacao {font-size: 2.1rem; font-weight: 700; color: #4C7DF0; line-height: 1.2;}
.subtitulo {color: #8A94B8; margin-bottom: 1.2rem;}
.kpis, .cidades {display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 16px; margin-bottom: 22px;}
.kpi {background: #fff; border-radius: 18px; padding: 16px 18px;
  box-shadow: 0 4px 18px rgba(80,110,200,.10);}
.kpi .rotulo {color: #8A94B8; font-size: .85rem;}
.kpi .valor {color: #2B3674; font-size: 1.55rem; font-weight: 700;}
.kpi .detalhe {color: #8A94B8; font-size: .85rem;}
.cidade {border-radius: 22px; padding: 18px 20px; color: #fff;
  box-shadow: 0 8px 22px rgba(80,110,200,.18);}
.cidade .topo {display: flex; justify-content: space-between; align-items: center; gap: 8px;}
.cidade .nome {font-size: 1.15rem; font-weight: 700;}
.cidade .pill {background: rgba(255,255,255,.93); color: #E5484D; font-size: .72rem;
  font-weight: 700; padding: 3px 9px; border-radius: 999px; white-space: nowrap;}
.cidade .clima {opacity: .95; margin-top: 2px;}
.cidade .temp {font-size: 3.2rem; font-weight: 700; line-height: 1.1; margin: 6px 0 10px;}
.cidade .linha {display: flex; justify-content: space-between; font-size: .92rem;
  padding: 3px 0; border-top: 1px solid rgba(255,255,255,.28);}
.cidade .rodape {font-size: .75rem; opacity: .9; margin-top: 10px;}
</style>
""",
    unsafe_allow_html=True,
)


def estilizar(fig):
    # Aplica o mesmo visual claro e limpo a TODOS os gráficos (fundo transparente, grade suave, legenda em cima). Chamar esta função evita repetir código
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#2B3674", size=13),
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", y=1.15, x=0, title_text=""),
    )
    fig.update_xaxes(gridcolor="#E8EDF8")
    fig.update_yaxes(gridcolor="#E8EDF8")
    return fig


def titulo(texto, subtitulo):
    # Cabeçalho padrão de cada tela (título grande azul + subtítulo cinza).
    st.markdown(
        f'<div class="saudacao">{texto}</div><div class="subtitulo">{subtitulo}</div>',
        unsafe_allow_html=True,
    )


# Componentes
def descricao(item):
    # Traduz os números em uma frase simples + emoji, para quem não entende de dados. A chuva tem prioridade sobre a temperatura.
    if item["chuva"] > 0:
        return "Chuva", "🌧️"
    t = item["temperatura"]
    if t >= 33:
        return "Muito quente", "☀️"
    if t >= 28:
        return "Quente", "🌤️"
    if t >= 22:
        return "Agradável", "⛅"
    return "Fresco", "☁️"


def gradiente(t):
    # Cor de fundo do cartão conforme a temperatura: laranja (>= 30), verde-água (22 a 29) ou azul (abaixo de 22). A cor "conta" o clima antes de ler o número.
    if t >= 30:
        return "linear-gradient(135deg,#FF9A5B,#FFC27A)"
    if t >= 22:
        return "linear-gradient(135deg,#3CC8A6,#7FE0C7)"
    return "linear-gradient(135deg,#5B8DEF,#8FB4FF)"


def html_cidade(item):
    # Monta o cartão de UMA cidade em HTML.
    # O selo "Acima do limite" é o ALERTA: aparece quando a temperatura atual passa do limite que o usuário configurou para aquela cidade.
    texto, emoji = descricao(item)
    pill = ('<span class="pill">Acima do limite</span>'
            if item["temperatura"] >= item["limite"] else "")
    # html.escape protege contra nomes de cidade com símbolos como < ou >.
    return (
        f'<div class="cidade" style="background:{gradiente(item["temperatura"])}">'
        f'<div class="topo"><span class="nome">{html.escape(item["cidade"])}</span>{pill}</div>'
        f'<div class="clima">{emoji} {texto}</div>'
        f'<div class="temp">{item["temperatura"]:.0f}°</div>'
        f'<div class="linha"><span>Umidade</span><b>{item["umidade"]:.0f}%</b></div>'
        f'<div class="linha"><span>Chuva</span><b>{item["chuva"]:g} mm</b></div>'
        f'<div class="linha"><span>Vento</span><b>{item["vento"]:.0f} km/h</b></div>'
        f'<div class="rodape">Atualizado às {item["data_hora"][11:16]} · limite {item["limite"]:g} °C</div>'
        f"</div>"
    )


def kpi(rotulo, valor, detalhe):
    # Um indicador (cartão branco): título pequeno, valor grande e detalhe.
    return (
        f'<div class="kpi"><div class="rotulo">{rotulo}</div>'
        f'<div class="valor">{valor}</div><div class="detalhe">{detalhe}</div></div>'
    )


def html_kpis(dados):
    # Calcula os 4 indicadores do topo do painel a partir dos dados do cache.
    quente = max(dados, key=lambda d: d["temperatura"])
    chuvosa = max(dados, key=lambda d: d["chuva"])
    media = sum(d["temperatura"] for d in dados) / len(dados)
    alertas = [d["cidade"] for d in dados if d["temperatura"] >= d["limite"]]

    if chuvosa["chuva"] > 0:
        k2 = kpi("Chovendo agora", html.escape(chuvosa["cidade"]), f'{chuvosa["chuva"]:g} mm na última hora')
    else:
        k2 = kpi("Chovendo agora", "Nenhuma cidade", "Sem chuva no momento")

    k4 = kpi(
        "Alertas de calor",
        str(len(alertas)),
        html.escape(", ".join(alertas)) if alertas else "Tudo dentro do limite",
    )
    return (
        kpi("Mais quente agora", html.escape(quente["cidade"]), f'{quente["temperatura"]:.1f} °C')
        + k2
        + kpi("Temperatura média", f"{media:.1f} °C", f"entre {len(dados)} cidades")
        + k4
    )


# Painel
# @st.fragment: faz esta seção ser redesenhada SOZINHA. Quando o usuário troca a cidade do mapa, só este trecho roda de novo, sem refazer a página inteira (e sem novas consultas ao Redis). Por isso a troca é instantânea.
@st.fragment
def secao_mapa(dados):
    st.markdown("### Mapa das cidades")
    nomes = ["Todas as cidades"] + [d["cidade"] for d in dados]
    escolhida = st.selectbox("Ver no mapa", nomes, key="cidade_mapa")
    df = pd.DataFrame(dados)

    if escolhida == "Todas as cidades":
        # Centraliza no meio das cidades e calcula um zoom que mostre todas: quanto mais espalhadas, menor o zoom (usa logaritmo da extensão).
        centro = {"lat": float(df["lat"].mean()), "lon": float(df["lon"].mean())}
        extensao = max(df["lat"].max() - df["lat"].min(), df["lon"].max() - df["lon"].min())
        zoom = max(1.5, min(9.0, 8 - math.log2(extensao + 0.01)))
        selecionada = None
    else:
        # Cidade escolhida: centraliza nela e aproxima o mapa.
        selecionada = next(d for d in dados if d["cidade"] == escolhida)
        centro = {"lat": float(selecionada["lat"]), "lon": float(selecionada["lon"])}
        zoom = 8

    col_mapa, col_info = st.columns([2, 1])
    with col_mapa:
        # Mapa com um ponto por cidade, colorido pela temperatura (mesma escala dos cartões). range_color fixa a escala de 15 a 40 °C para as cores não mudarem de significado conforme os dados.
        fig = px.scatter_map(
            df,
            lat="lat",
            lon="lon",
            color="temperatura",
            text="cidade",
            hover_name="cidade",
            hover_data={"temperatura": ":.1f", "umidade": ":.0f", "chuva": ":.1f",
                        "vento": ":.0f", "lat": False, "lon": False},
            labels={"temperatura": "Temperatura (°C)", "umidade": "Umidade (%)",
                    "chuva": "Chuva (mm)", "vento": "Vento (km/h)"},
            color_continuous_scale=ESCALA_TEMP,
            range_color=[15, 40],
            zoom=zoom,
            center=centro,
            height=430,
            map_style="carto-positron",
        )
        fig.update_traces(marker=dict(size=20), mode="markers+text", textposition="top center")
        fig.update_layout(
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            coloraxis_colorbar=dict(title="°C", thickness=12, len=0.6),
        )
        st.plotly_chart(fig, key="mapa_cidades")

    with col_info:
        # Ao lado do mapa: o cartão detalhado da cidade escolhida.
        if selecionada:
            st.markdown(html_cidade(selecionada), unsafe_allow_html=True)
        else:
            st.info("Escolha uma cidade na lista acima para aproximar o mapa e ver os detalhes dela.")


def cabecalho():
    # Saudação conforme a hora do dia + data por extenso.
    agora = datetime.now()
    saudacao = "Bom dia" if agora.hour < 12 else "Boa tarde" if agora.hour < 18 else "Boa noite"
    data = f"{DIAS[agora.weekday()].capitalize()}, {agora.day} de {MESES[agora.month - 1]} de {agora.year}"
    titulo(f"{saudacao}! Veja como está o tempo agora", f"{data} · dados ao vivo da Open-Meteo")


def pagina_painel():
    cabecalho()

    #  - "Coletar dados agora": roda o coletor, que grava no Supabase e APAGA o cache.
    #  - "Limpar cache do Redis": apaga o cache à mão, forçando a próxima consulta a ir ao Supabase (aparece "Supabase (banco)" e o tempo maior).
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Coletar dados agora"):
        with st.spinner("Buscando na API e gravando no Supabase..."):
            coletar_tudo()
        st.toast("Dados novos gravados no Supabase. Cache do Redis limpo.")
    if b2.button("Limpar cache do Redis"):
        invalidar_cache()
        st.toast("Cache limpo. A próxima consulta vai ao Supabase.")

    # Ponto Central da Integração: pede os dados ao serviço, que decide sozinho entre Redis (rápido) e Supabase (lento). A tela mostra a origem e o tempo.
    dados, origem, ms = obter_painel()
    st.caption(f"Fonte dos dados: {origem} · tempo de resposta: {ms:.0f} ms")

    if not dados:
        st.warning("Ainda não há leituras. Clique em 'Coletar dados agora'.")
        return origem

    # Monta todos os cartões num único bloco HTML dentro de uma grade (grid), o que mantém os cartões alinhados.
    cartoes = "".join(html_cidade(d) for d in dados)
    st.markdown(
        f'<div class="kpis">{html_kpis(dados)}</div><div class="cidades">{cartoes}</div>',
        unsafe_allow_html=True,
    )
    secao_mapa(dados)
    # Devolve a origem para o menu contar a consulta como HIT ou MISS.
    return origem


# Histórico
# Também é um fragmento: trocar cidades, variável ou aba redesenha só esta seção, usando os dados que já estão na memória (sem nova consulta).
@st.fragment
def secao_historico(linhas):
    df = pd.DataFrame(linhas)
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    nomes = sorted(df["cidade"].unique())

    c1, c2 = st.columns([2, 1])
    escolhidas = c1.multiselect("Cidades", nomes, default=nomes)
    rotulo = c2.selectbox("O que mostrar", list(VARIAVEIS))
    coluna = VARIAVEIS[rotulo]

    if not escolhidas:
        st.info("Escolha pelo menos uma cidade.")
        return
    # Filtra no pandas (na memória), sem voltar ao banco.
    dff = df[df["cidade"].isin(escolhidas)]

    aba1, aba2, aba3 = st.tabs(["Evolução", "Mínima e máxima por dia", "Chuva por dia"])

    with aba1:
        # Gráfico de linhas: ideal para mostrar como algo muda ao longo do tempo. line_shape="spline" suaviza as curvas.
        fig = px.line(
            dff, x="data_hora", y=coluna, color="cidade", line_shape="spline",
            color_discrete_sequence=CORES,
            labels={"data_hora": "", coluna: rotulo, "cidade": "Cidade"},
        )
        fig.update_traces(line=dict(width=3))
        estilizar(fig)
        fig.update_layout(hovermode="x unified")
        fig.update_xaxes(tickformat="%d/%m")
        st.plotly_chart(fig, key="grafico_evolucao")
        st.caption("Cada linha é uma cidade. Passe o mouse sobre o gráfico para ver os valores de cada horário.")

    with aba2:
        # Resume as centenas de leituras em 2 números por dia (mínima e máxima). Mais fácil de entender do que uma linha com muitos pontos.
        cidade = st.selectbox("Cidade", escolhidas, key="cidade_minmax")
        dc = df[df["cidade"] == cidade].copy()
        dc["dia"] = dc["data_hora"].dt.normalize()  # normalize: tira a hora, fica só o dia
        resumo = dc.groupby("dia")["temperatura"].agg(["min", "max"]).reset_index()
        fig = go.Figure()
        fig.add_bar(x=resumo["dia"], y=resumo["min"], name="Mínima do dia", marker_color="#7FA8FF")
        fig.add_bar(x=resumo["dia"], y=resumo["max"], name="Máxima do dia", marker_color="#FF9F5A")
        fig.update_traces(texttemplate="%{y:.0f}°", textposition="outside", cliponaxis=False)
        estilizar(fig)
        fig.update_layout(barmode="group", yaxis_title="Temperatura (°C)")
        fig.update_xaxes(tickformat="%d/%m")
        st.plotly_chart(fig, key="grafico_minmax")
        st.caption("A barra azul é a hora mais fria de cada dia e a laranja é o pico de calor.")

    with aba3:
        # Soma a chuva de cada hora para saber quanto choveu em cada dia.
        chuva = (
            dff.assign(dia=dff["data_hora"].dt.normalize())
            .groupby(["dia", "cidade"])["chuva"].sum().reset_index()
        )
        if chuva["chuva"].sum() == 0:
            st.info("Não choveu nas cidades escolhidas neste período.")
        else:
            fig = px.bar(
                chuva, x="dia", y="chuva", color="cidade", barmode="group",
                color_discrete_sequence=CORES,
                labels={"dia": "", "chuva": "Chuva no dia (mm)", "cidade": "Cidade"},
            )
            estilizar(fig)
            fig.update_xaxes(tickformat="%d/%m")
            st.plotly_chart(fig, key="grafico_chuva")
            st.caption("Total de chuva em milímetros que caiu em cada dia.")


def pagina_historico():
    titulo("Histórico", "Como o tempo mudou nos últimos dias")
    periodo = st.selectbox("Período", list(PERIODOS), index=1)
    # O histórico também passa pelo cache do Redis (obter_historico).
    with st.spinner("Carregando histórico..."):
        linhas, origem, ms = obter_historico(periodo)
    st.caption(f"Fonte dos dados: {origem} · tempo de resposta: {ms:.0f} ms")

    if not linhas:
        st.warning("Nenhuma leitura neste período.")
        return origem
    secao_historico(linhas)
    return origem


# Raking
def grafico_ranking(itens, unidade, escala, chave):
    # Barras horizontais com o valor escrito em cada barra: leitura direta, sem precisar entender eixos.
    df = pd.DataFrame(itens, columns=["cidade", "valor"])
    fig = px.bar(df, x="valor", y="cidade", orientation="h", color="valor",
                 color_continuous_scale=escala)
    estilizar(fig)
    fig.update_traces(texttemplate="%{x:.1f} " + unidade, textposition="outside", cliponaxis=False)
    # autorange="reversed" coloca o 1º colocado no topo da lista.
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(visible=False)
    fig.update_coloraxes(showscale=False)
    fig.update_layout(height=90 + 55 * len(df))
    st.plotly_chart(fig, key=chave)


def pagina_ranking():
    titulo("Ranking das cidades", "Quem está mais quente e quem está recebendo chuva")
    # A lista já vem pronta e ordenada dos sorted sets, sem nenhuma consulta ao Supabase e sem ordenar no código.
    quentes = top_ranking("ranking:temperatura")
    chuvosas = top_ranking("ranking:chuva")
    if not quentes:
        st.info("O ranking ainda está vazio. Vá ao Painel e clique em 'Coletar dados agora'.")
        return

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Mais quentes agora")
        grafico_ranking(quentes, "°C", ESCALA_TEMP, "rank_temp")
    with c2:
        st.markdown("### Mais chuvosas agora")
        if all(valor == 0 for _, valor in chuvosas):
            st.info("Nenhuma cidade com chuva neste momento.")
        else:
            grafico_ranking(chuvosas, "mm", ESCALA_CHUVA, "rank_chuva")
    st.caption("Esta lista vem do Redis (sorted set) e é reordenada automaticamente a cada coleta.")


# Cidades
def carregar_dados_cidade(cidade_id, nome, lat, lon):
    """Traz 7 dias de histórico e a leitura atual da cidade para o Supabase.

    Roda logo depois de cadastrar (ou mover) uma cidade, para ela já aparecer
    no painel e nos gráficos sem esperar a próxima coleta automática.
    """
    # 1) Histórico de 7 dias vindo da API Open-Meteo.
    linhas = buscar_historico(lat, lon)
    registros = [{"cidade_id": cidade_id, **linha} for linha in linhas]
    if registros:
        # UPSERT = "insere ou atualiza". A tabela tem a regra (cidade_id, data_hora) única; se a leitura já existe, ela é atualizada em vez de duplicada.
        supabase.table("leituras").upsert(registros, on_conflict="cidade_id,data_hora").execute()
    # 2) Leitura atual.
    atual = buscar_clima(lat, lon)
    salvar_leitura(cidade_id, atual)
    # 3) Atualiza o ranking no Redis e limpa o cache, pois os dados mudaram.
    atualizar_rankings([{"cidade": nome, **atual}])
    invalidar_cache()


def pagina_cidades():
    titulo("Cidades", "Cadastre, edite ou exclua as cidades monitoradas")

    # Mensagem guardada antes de um st.rerun(): como a página é recarregada, o aviso é guardado em session_state para ainda aparecer depois.
    if "aviso" in st.session_state:
        st.success(st.session_state.pop("aviso"))

    aba_add, aba_edit, aba_lista = st.tabs(
        ["Adicionar cidade", "Editar ou excluir", "Cidades monitoradas"]
    )

    with aba_add:
        st.caption("Dica: no Google Maps, clique com o botão direito sobre a cidade e copie "
                   "as coordenadas (latitude e longitude).")
        # st.form agrupa os campos: nada é processado até clicar no botão de envio.
        with st.form("form_adicionar"):
            nome = st.text_input("Nome da cidade", placeholder="Ex.: Campo Maior")
            c1, c2, c3 = st.columns(3)
            # value=None deixa os campos VAZIOS no início. Assim o usuário nunca envia "0" sem querer
            lat = c1.number_input("Latitude", value=None, format="%.4f", step=0.0001,
                                  placeholder="Ex.: -4.8207")
            lon = c2.number_input("Longitude", value=None, format="%.4f", step=0.0001,
                                  placeholder="Ex.: -42.1721")
            limite = c3.number_input("Limite de alerta (°C)", value=38.0, step=1.0)
            enviar = st.form_submit_button("Adicionar cidade")

        if enviar:
            # TRAVA: valida antes de gravar (nome vazio, coordenadas zeradas etc.).
            erro = validar_cidade(nome, lat, lon)
            if erro:
                st.error(erro)
            else:
                try:
                    cidade = adicionar_cidade(nome, lat, lon, limite)
                except Exception as e:
                    # 23505 é o código do PostgreSQL para "valor duplicado": o nome da cidade é único no banco.
                    if "23505" in str(e):
                        st.warning(f"A cidade {nome.strip()} já está cadastrada.")
                    else:
                        st.error(f"Erro ao cadastrar a cidade: {e}")
                else:
                    try:
                        with st.spinner("Buscando os dados da cidade..."):
                            carregar_dados_cidade(cidade["id"], cidade["nome"], lat, lon)
                        st.success(f"{cidade['nome']} adicionada, com 7 dias de histórico. "
                                   "Ela já aparece no Painel.")
                    except Exception as e:
                        st.warning(f"{cidade['nome']} foi cadastrada, mas não consegui buscar os "
                                   f"dados agora ({e}). Use 'Coletar dados agora' no Painel.")

    # Lista lida depois do cadastro, para já incluir a cidade recém-criada.
    cidades = listar_cidades()

    with aba_edit:
        if not cidades:
            st.info("Nenhuma cidade cadastrada.")
        else:
            por_nome = {c["nome"]: c for c in cidades}
            escolhida = st.selectbox("Escolha a cidade", list(por_nome), key="cidade_edicao")
            c = por_nome[escolhida]

            # O id da cidade faz parte do nome do formulário e das chaves dos campos. Assim, ao trocar de cidade, os campos são recriados com os valores dela, em vez de manter os da cidade anterior.
            with st.form(f"form_editar_{c['id']}"):
                novo_nome = st.text_input("Nome", value=c["nome"], key=f"nome_{c['id']}")
                e1, e2, e3 = st.columns(3)
                nova_lat = e1.number_input("Latitude", value=float(c["latitude"]),
                                           format="%.4f", step=0.0001, key=f"lat_{c['id']}")
                nova_lon = e2.number_input("Longitude", value=float(c["longitude"]),
                                           format="%.4f", step=0.0001, key=f"lon_{c['id']}")
                novo_limite = e3.number_input("Limite de alerta (°C)",
                                              value=float(c["limite_alerta_temp"]),
                                              step=1.0, key=f"lim_{c['id']}")
                ativa = st.checkbox("Monitorar esta cidade", value=c["ativa"], key=f"ativa_{c['id']}")
                salvar = st.form_submit_button("Salvar alterações")

            if salvar:
                erro = validar_cidade(novo_nome, nova_lat, nova_lon)
                if erro:
                    st.error(erro)
                else:
                    # Descobre o que mudou, pois isso decide o que refazer: mudou o LOCAL -> as leituras antigas eram de outro lugar e são refeitas;
                    # mudou só o NOME -> o ranking do Redis precisa ser atualizado.
                    mudou_local = (round(nova_lat, 4), round(nova_lon, 4)) != (
                        round(c["latitude"], 4), round(c["longitude"], 4))
                    mudou_nome = novo_nome.strip() != c["nome"]
                    try:
                        atualizar_cidade(c["id"], c["nome"], novo_nome, nova_lat, nova_lon,
                                         novo_limite, ativa)
                    except Exception as e:
                        if "23505" in str(e):
                            st.warning(f"Já existe outra cidade chamada {novo_nome.strip()}.")
                        else:
                            st.error(f"Erro ao salvar: {e}")
                    else:
                        if ativa and (mudou_local or mudou_nome):
                            with st.spinner("Atualizando os dados da cidade..."):
                                if mudou_local:
                                    apagar_leituras(c["id"])
                                carregar_dados_cidade(c["id"], novo_nome.strip(), nova_lat, nova_lon)
                        st.session_state["aviso"] = f"{novo_nome.strip()} atualizada."
                        st.session_state.pop("cidade_edicao", None)
                        # st.rerun recarrega a página para a lista refletir a mudança.
                        st.rerun()

            st.divider()
            st.markdown("**Excluir cidade**")
            # O botão de excluir só funciona depois de marcar a confirmação (disabled=not confirmar), para evitar exclusões acidentais.
            confirmar = st.checkbox(f"Quero excluir {c['nome']} e todo o histórico dela",
                                    key=f"confirma_{c['id']}")
            if st.button("Excluir cidade", disabled=not confirmar):
                excluir_cidade(c["id"], c["nome"])
                st.session_state["aviso"] = f"{c['nome']} foi excluída."
                st.session_state.pop("cidade_edicao", None)
                st.rerun()

    with aba_lista:
        if not cidades:
            st.info("Nenhuma cidade cadastrada.")
        else:
            tabela = pd.DataFrame(cidades)[["nome", "latitude", "longitude",
                                            "limite_alerta_temp", "ativa"]]
            tabela.columns = ["Cidade", "Latitude", "Longitude", "Limite de alerta (°C)", "Monitorada"]
            tabela["Monitorada"] = tabela["Monitorada"].map({True: "Sim", False: "Não"})
            st.dataframe(tabela, hide_index=True)


# Menu
# Este trecho final é o "roteador": mostra o menu lateral e executa a função da tela escolhida.
with st.sidebar:
    st.markdown('<div class="logo">Monitor de Clima</div>', unsafe_allow_html=True)
    pagina = st.radio("Menu", ["Painel", "Histórico", "Ranking", "Cidades"],
                      label_visibility="collapsed")

# "origem" diz de onde vieram os dados da tela (Redis ou Supabase).
# Ranking e Cidades não usam esse cache, então ficam sem origem (None).
origem = None
if pagina == "Painel":
    origem = pagina_painel()
elif pagina == "Histórico":
    origem = pagina_historico()
elif pagina == "Ranking":
    pagina_ranking()
else:
    pagina_cidades()

# Contadores do Cache: registra a consulta como HIT ou MISS no Redis e traz os totais. É o que mostra, ao vivo, o quanto o cache está ajudando.
hits, miss = registrar_consulta(origem)
with st.sidebar:
    st.divider()
    st.caption("Cache (Redis)")
    m1, m2 = st.columns(2)
    m1.metric("Do cache", hits)
    m2.metric("Do banco", miss)
    total = hits + miss
    if total:
        # Taxa de acerto do cache: quantas consultas foram atendidas pelo Redis.
        st.caption(f"{hits / total * 100:.0f}% das consultas vieram do cache")