# NEXTTHOUSE D2B2B — exportação LGPD criptografada

Status: biblioteca local e hermética, sem endpoint, dados reais ou wiring.

## Contrato entregue

- Exporta somente solicitações `access` ou `correction` verificadas e sem legal hold.
- Liga o pedido ao lead por referência pseudônima antes de ler qualquer dado.
- Serializa uma allowlist limitada do lead e dos toques de atribuição.
- Usa AES-256-GCM, nonce aleatório e AAD ligada a organização, pedido e artefato.
- Exige chave de 32 bytes fornecida pelo chamador e nunca a persiste.
- Grava em diretório privado `0700`, recusa symlink e cria artefato `0600` com `O_EXCL`.
- Limita plaintext a 1 MiB e TTL a no máximo 24 horas.
- Registra somente digest opaco do artefato no ledger append-only.
- Em falha após criação, remove o artefato e reverte o evento/transição.

## Limites deliberados

- Não existe endpoint de download ou entrega ao titular.
- Não existe scheduler de expiração/limpeza nem custódia de chave.
- A exportação ainda não inclui contatos, anexos, comentários, tarefas, oportunidades ou dados em sistemas externos.
- Apagar o arquivo criptografado não equivale a apagar os registros originais.
- Solicitações de exclusão são recusadas por este exportador.

Antes de staging serão necessários inventário completo, chave em KMS/HSM, expiração operacional, trilha de download, autenticação reforçada, segregação de funções e teste PostgreSQL.
