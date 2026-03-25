# 🚌 Gestão de Pátio — Sambaíba (Garagem 3)

**Otimização Logística e Celeridade na Liberação de Frota**

🔗 **[Acessar o Protótipo Operacional](https://alisson84martins.github.io/Gestao-Patio-Sambaiba/)**

---

## 🎯 Visão Estratégica: O Fim do Gargalo na Soltura
"Em transportes urbanos, cada minuto no pátio é um minuto a menos na rua. Este sistema nasceu para eliminar gargalos, acelerar a saída da frota e garantir que cada ônibus chegue ao ponto certo, na hora certa."**. A soltura da frota é o momento mais crítico da operação diária. Processos manuais, pranchetas e desvios de comunicação geram lentidão, veículos retidos de forma invisível e atrasos no início da linha.

Este protótipo foi projetado com uma mentalidade pragmática para resolver essa dor exata: digitalizar o pátio da Garagem 3 Sambaíba, garantindo **agilidade, precisão e celeridade** na alocação e liberação dos ônibus.

## 🚀 Funcionalidades de Impacto Imediato
A ferramenta foi codificada para se adaptar ao fluxo da garagem, e não o contrário:

* **Marcação em Bloco Orientada:** Alocação de dezenas de veículos em segundos, respeitando a física do pátio (sentido Frente → Fundo e vice-versa).
* **Gestão de Exceções em Tempo Real:** Painéis de alerta para veículos Presos (Manutenção) e Amostrais (SPTRANS), tirando o veículo da fila e evitando falhas na soltura.
* **Resiliência Operacional (Offline-First):** Pátios de garagem possuem zonas cegas de conectividade. O sistema utiliza `localStorage` para garantir que a operação não pare, mesmo sem internet.
* **Automação de Escala:** Motor de leitura embutido (SheetJS) que importa planilhas Excel/CSV da escala de trabalho, cruzando frota, linha e horário instantaneamente.

## 🏗️ Arquitetura e Escalabilidade (O Futuro)
Este protótipo é a fundação. O código foi estruturado em Client-Side (HTML5, CSS3, JS Vanilla) para validação imediata da regra de negócio, mas a arquitetura deixa as portas abertas para a evolução corporativa:

* **Pronto para APIs:** A estrutura de dados (State Management) foi isolada. É simples substituir o armazenamento local por chamadas a um banco de dados relacional (PostgreSQL) via API (Python/Node.js).
* **Multi-Telas e Sincronização:** Base preparada para futura implementação de WebSockets, permitindo que múltiplos despachantes operem e vejam atualizações em tempo real.
* **Métricas (BI):** Facilidade para futura extração de dados (tempos médios de pátio, reincidência de quebras) para painéis gerenciais.

## 👤 O Arquiteto por Trás do Código
Este sistema é o resultado da união entre o conhecimento operacional e a engenharia de software. 

Sou um profissional veterano do setor de transportes, combinando a disciplina tática e o conhecimento logístico do chão de fábrica (Técnico em Logística) com o raciocínio lógico da Análise e Desenvolvimento de Sistemas. Meu foco é analisar processos manuais pesados, identificar gargalos e arquitetar soluções digitais diretas, resilientes e escaláveis.

---
*Protótipo em fase de validação operacional (Alpha).*
