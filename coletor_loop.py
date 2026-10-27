# coletor_loop.py  -  COLETA AUTOMÁTICA A CADA 15 MINUTOS

# Roda em um segundo terminal, ao lado do Streamlit, e repete a coleta sem ninguém precisar clicar. Assim o Supabase vai recebendo leituras novas e o histórico cresce sozinho. A Open-Meteo atualiza seus dados a cada 15 minutos, por isso esse é o intervalo escolhido.

# Para usar:   python coletor_loop.py     (Ctrl + C para parar)
# O computador precisa ficar ligado e sem entrar em suspensão.
#---------------------------------------------------------------------------------------------------------------------------------------------

import time
from datetime import datetime

from coletor import coletar_tudo

INTERVALO_MIN = 15

# Laço infinito: coleta, espera 15 minutos, coleta de novo...
while True:
    print(f"\n[{datetime.now():%d/%m %H:%M}] Coletando...")
    # Cada coleta grava no Supabase, atualiza o ranking e APAGA o cache do Redis (isso acontece dentro de coletar_tudo). Por isso, logo depois de uma coleta, a primeira consulta do painel vem do Supabase.
    coletar_tudo()
    print(f"Próxima coleta em {INTERVALO_MIN} minutos. Ctrl + C para parar.")
    # sleep recebe segundos, então multiplicamos os minutos por 60.
    time.sleep(INTERVALO_MIN * 60)