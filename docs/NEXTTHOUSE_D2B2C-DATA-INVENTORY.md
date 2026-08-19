# NEXTTHOUSE D2B2C — inventário e retenção

Status: contrato local fail-closed. Nenhum prazo está juridicamente aprovado e nenhuma exclusão é executável.

## Inventário v1

| Entidade | Classificação | Exportação atual | Dependências de exclusão |
|---|---|---|---|
| Lead | Identificadores e perfil comercial | Allowlist de dados básicos e acompanhamento | Contatos, contas, oportunidades, tarefas, anexos e atividades |
| Attribution touch | Atribuição de marketing pseudônima | Origem, campanha, momento e base legal | Lead, campanha e evidence store |
| Privacy request | Governança pseudônima | Estado operacional sem digests internos | Eventos e legal hold |
| Privacy event | Auditoria de segurança/compliance | Tipo, sequência, reason code e data | Pedido, legal hold e auditoria |
| Contact | Identificadores e relacionamento | Contatos explicitamente ligados ao lead | Conta, oportunidades, tarefas e anexos |
| Task | Workflow e texto livre | Tarefas com FK direta para o lead | Pai, anexos e atividades |
| Attachment metadata | Metadados de arquivo | Nome e data; binário excluído | Storage, malware e direitos |
| Activity | Metadados de auditoria | Ação, tipo e data; texto livre excluído | Requisitos de auditoria |

## Propostas de retenção — não aprovadas

- Lead: encerramento da relação mais período jurídico aplicável.
- Atribuição: último touch mais 13 meses.
- Solicitação e eventos LGPD: encerramento mais 5 anos.

Esses valores são hipóteses para análise do Privacy/Legal, não regras de sistema. `retention_approved=False` em todas as entidades e o guard de exclusão falha fechado enquanto qualquer aprovação estiver pendente.

## Lacunas do inventário

Ainda precisam de lineage comprovável: contas, oportunidades, comentários, formulários, arquivos binários, WordPress, Metricool, redes sociais e quaisquer dados do Portal. O modelo atual removeu os vínculos de conversão lead→conta/contato/oportunidade; conta e oportunidade não podem ser inferidas por nome ou e-mail. Kommo permanece fora do CRM NEXTTHOUSE e restrito à campanha Cinema da agência.
