# NEXTTHOUSE D2B1 — paginação e intake LGPD

Status: implementação local, desconectada do Portal e de produção.

## Entregue

- Listagens de campanhas, atribuições e solicitações LGPD usam cursor opaco, com 50 registros por página.
- `GET|POST /api/leads/privacy-requests/` é autenticado, restrito ao administrador da organização e idempotente.
- O intake aceita `access`, `correction` e `deletion`, mas registra somente o estado `submitted`.
- O banco guarda uma referência pseudônima derivada do UUID do lead, nunca e-mail, telefone ou o UUID bruto na resposta.
- A tabela é isolada por organização no ORM e registrada para RLS no PostgreSQL.

## Limite de segurança

Este gate **não executa acesso, correção ou exclusão**, não altera o lead e não oferece endpoint de transição de estado. `subject_ref_digest` é pseudônimo, não anônimo, e continua sujeito a retenção e controles LGPD.

O workflow de verificação de identidade, legal hold, aprovação, exportação, correção, exclusão, evidência e dupla revisão será um gate posterior. Portal, dados reais, merge e deploy também permanecem bloqueados.

## Contrato de paginação

As respostas de listagem seguem:

```json
{
  "next": "cursor opaco ou null",
  "previous": "cursor opaco ou null",
  "results": []
}
```

Clientes não devem interpretar nem persistir o conteúdo do cursor além da navegação imediata.
