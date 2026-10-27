-- banco.sql  -  Estrutura do banco de dados do Monitor de Clima (Supabase / PostgreSQL)

-- Como usar: no painel do Supabase, abra o "SQL Editor", clique em "New query",
-- cole TODO este arquivo e clique em "Run".
-- Se o Supabase avisar sobre RLS, escolha "Run and enable RLS".
-- O script pode ser executado mais de uma vez sem causar erro.
-- =============================================================================



-- Tabela de cidades monitoradas
create table if not exists cidades (
    id                 bigint generated always as identity primary key,
    nome               text             not null unique,           -- não permite duas cidades com o mesmo nome
    latitude           double precision not null,
    longitude          double precision not null,
    limite_alerta_temp double precision not null default 38,      -- acima disso o painel mostra o alerta
    ativa              boolean          not null default true      -- false = deixa de ser coletada
);


-- Tabela de leituras do clima (uma linha por cidade e horário)
create table if not exists leituras (
    id          bigint generated always as identity primary key,
    cidade_id   bigint not null references cidades(id) on delete cascade,  -- apagar a cidade apaga as leituras dela
    temperatura double precision,   -- graus Celsius
    umidade     double precision,   -- porcentagem
    chuva       double precision,   -- milímetros
    vento       double precision,   -- km/h
    data_hora   timestamp   not null,                -- horário da leitura informado pela API
    criado_em   timestamptz not null default now(),  -- quando a linha foi gravada
    unique (cidade_id, data_hora)                    -- impede leitura duplicada (permite usar UPSERT)
);


-- Segurança: RLS (Row Level Security)

-- Com o RLS ligado e sem políticas, as chaves públicas (anon) não leem nem gravam nada. A aplicação usa a chave "secret", que ignora o RLS.
alter table cidades  enable row level security;
alter table leituras enable row level security;


-- View com a ÚLTIMA leitura de cada cidade

-- O painel precisa só da leitura mais recente de cada cidade. O DISTINCT ON deixa o próprio banco escolher essa linha, em vez de a aplicação fazer uma consulta por cidade. security_invoker faz a view respeitar o RLS da tabela.
create or replace view ultimas_leituras with (security_invoker = true) as
select distinct on (cidade_id)
       cidade_id, temperatura, umidade, chuva, vento, data_hora
from leituras
order by cidade_id, data_hora desc;


-- Cidades iniciais (opcional: também podem ser cadastradas pela tela do app)
insert into cidades (nome, latitude, longitude) values
    ('Teresina',     -5.0892, -42.8019),
    ('Campo Maior',  -4.8207, -42.1721),
    ('Fortaleza',    -3.7319, -38.5267),
    ('São Paulo',   -23.5505, -46.6333),
    ('Porto Alegre', -30.0346, -51.2177)
on conflict (nome) do nothing;
