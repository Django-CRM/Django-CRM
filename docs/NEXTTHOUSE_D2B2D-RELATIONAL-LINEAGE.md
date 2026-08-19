# NEXTTHOUSE D2B2D — lineage relacional do titular

Status: implementação local limitada a relações comprovadas pelo schema.

## Incluído na exportação criptografada

- Lead solicitado.
- Toques de atribuição ligados ao lead.
- Contatos explicitamente ligados pelo relacionamento `Lead.contacts`.
- Tarefas com FK direta `Task.lead`.
- Metadados de anexos cujo GenericForeignKey aponta diretamente para o lead.
- Atividades com `entity_type=Lead` e `entity_id` correspondente.

Arquivos binários, caminhos internos, metadata JSON e descrições de atividade não entram no pacote.

## Não inferido

Contas e oportunidades não possuem atualmente um vínculo persistente com o lead convertido. Correspondência por nome, e-mail, telefone ou empresa poderia misturar pessoas e organizações, portanto falha fechado. Para incluí-las será necessário restaurar provenance de conversão com identificadores imutáveis e migração auditável.

Comentários, formulários, WordPress, Metricool, redes sociais e Portal ainda exigem adapters de lineage próprios. Kommo continua restrito à campanha Cinema da agência e fora da autoridade deste CRM.
