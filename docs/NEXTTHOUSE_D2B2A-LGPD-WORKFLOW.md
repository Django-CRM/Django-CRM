# NEXTTHOUSE D2B2A — workflow LGPD auditável

Status: implementação local, sem Portal, PII real ou execução dos direitos.

## Escopo entregue

- Estado versionado com concorrência otimista.
- Ações permitidas: verificar identidade por referência opaca, colocar ou retirar legal hold e rejeitar sem execução.
- Eventos sanitizados e append-only: tipo, sequência, reason code allowlisted e digests de ator/evidência.
- RLS para pedido e evento; PostgreSQL também bloqueia `UPDATE` e `DELETE` dos eventos por trigger.
- A solicitação nasce com um evento `submitted` na mesma transação.

## Proibições deste gate

Não existem ações de aprovação, conclusão, exportação de PII ou exclusão. Uma solicitação de exclusão continua sendo somente um pedido. Nenhum evento contém e-mail, telefone, texto livre ou evidência bruta.

## Gates posteriores

1. Adaptador confiável de verificação de identidade e segregação de funções.
2. Inventário de dados e exportação isolada, criptografada, expirada e auditável.
3. Política aprovada de retenção, exceções e legal hold.
4. Backup e restore provados antes de qualquer executor destrutivo.
5. Aprovação jurídica/Privacy e teste em PostgreSQL antes de integração com o Portal.
