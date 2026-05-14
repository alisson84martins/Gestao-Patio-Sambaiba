# Banco de Dados — Sistema de Gestão de Pátio Sambaíba v3.0

Esta pasta contém toda a documentação, scripts e seeds do banco de dados PostgreSQL da v3.0.

## Estado atual (02/05/2026)

✅ **Especificação fechada** — `01-especificacao-banco-v3.md`

⏭️ Próximas etapas:
- DER lógico revisado (HTML)
- Scripts DDL (migrations/)
- Seeds iniciais (seeds/)
- Dicionário de dados (XLSX)

## Decisões principais

- **SGBD:** PostgreSQL 15+
- **Banco:** `gestao_patio_sambaiba`
- **Tabelas:** 12 (8 operacionais + 3 catálogos + 1 sistema)
- **Chave primária:** UUID em todas
- **Modelo de operação:** Híbrido offline-first (IndexedDB + sync)
- **Auditoria:** Completa em operacionais; apenas `criado_em` em catálogos
- **Soft delete:** Em escala, alerta, ficha_manutencao

## Como ler a especificação

Comece por `01-especificacao-banco-v3.md`. Esse documento responde:
- Quais tabelas existem e por quê
- Quais regras de negócio cada uma cobre
- Como filas, posições especiais e manutenção convivem na mesma estrutura
- Como o histórico funciona sem precisar "limpar" entre dias
- Quais índices aplicar no DDL inicial

## Convenções

- Identificadores em **snake_case** (ex: `numero_frota`, `alocacao_patio`)
- Timestamps em UTC (`TIMESTAMP WITH TIME ZONE`)
- ENUMs nativos do PostgreSQL para domínios fechados
- UUID v4 (gerado por `gen_random_uuid()`)
- Migrations numeradas sequencialmente (001, 002, …)

---

*Sambaíba Transportes Urbanos · Garagem 3 · 2026*
