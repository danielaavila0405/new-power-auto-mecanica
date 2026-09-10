# New Power Auto Mecânica — Sistema de Gestão de Oficina

Aplicação web desenvolvida para digitalizar e organizar a operação da New Power Auto Mecânica, centralizando o cadastro de clientes e veículos, ordens de serviço, peças, serviços e informações financeiras.

O projeto foi desenvolvido em Python e Flask, com banco de dados SQLite e interface web responsiva, e posteriormente implantado em ambiente cloud utilizando Railway.

---

## Sobre o projeto

A New Power Auto Mecânica realizava parte do controle operacional de forma manual, o que dificultava a organização das ordens de serviço, o acompanhamento dos veículos e a visualização das informações financeiras.

A solução desenvolvida foi uma aplicação web própria, adaptada às necessidades da oficina e construída considerando os processos reais da operação.

---

## Objetivos

- Digitalizar o processo de abertura e acompanhamento das Ordens de Serviço
- Centralizar informações de clientes e veículos
- Manter o histórico dos serviços realizados nos veículos
- Registrar peças e serviços utilizados em cada OS
- Organizar informações de pagamento e faturamento
- Facilitar a geração e o compartilhamento das Ordens de Serviço
- Disponibilizar indicadores para acompanhamento da operação
- Permitir acesso controlado por usuários

---

## Principais funcionalidades

### Clientes
- Cadastro de clientes
- Consulta de clientes
- Associação de veículos ao cliente

### Veículos
- Cadastro de veículos
- Associação do veículo ao respectivo cliente
- Consulta do histórico relacionado às Ordens de Serviço

### Ordens de Serviço
- Criação de novas OS
- Inclusão de peças
- Inclusão de serviços
- Definição do responsável pela mão de obra
- Definição da forma de pagamento
- Controle de status
- Edição de OS
- Visualização detalhada
- Histórico das ordens
- Cálculo automático dos valores
- Geração de documento em PDF
- Compartilhamento por WhatsApp

### Financeiro
- Consulta das Ordens de Serviço finalizadas
- Filtros por período
- Visualização do faturamento
- Análise por forma de pagamento

### Dashboard
- Indicadores da operação
- Faturamento
- Distribuição por forma de pagamento
- Análise por responsável
- Evolução mensal

### Usuários e segurança
- Sistema de login
- Controle de acesso
- Perfis de usuário
- Alteração de senha
- Recuperação/reset de acesso

---

## Tecnologias utilizadas

### Backend
- Python
- Flask

### Banco de dados
- SQLite

### Frontend
- HTML5
- CSS3
- JavaScript

### Visualização de dados
- Chart.js

### Geração de documentos
- ReportLab

### Versionamento
- Git
- GitHub

### Deploy
- Railway

## Banco de dados

O sistema utiliza **SQLite** como banco de dados relacional, responsável pelo armazenamento das informações da aplicação.

Principais tabelas:

- `clientes` — cadastro dos clientes.
- `veiculos` — cadastro dos veículos e vínculo com os clientes.
- `ordens_servico` — informações principais das Ordens de Serviço.
- `pecas_os` — peças utilizadas em cada Ordem de Serviço.
- `servicos_os` — serviços e valores de mão de obra.
- `usuarios` — usuários do sistema e informações de acesso.

Os relacionamentos entre clientes, veículos e Ordens de Serviço permitem manter o histórico dos atendimentos realizados para cada veículo.

## Regras de negócio

A aplicação foi desenvolvida considerando as regras e necessidades da operação da oficina.

- Peças e serviços são registrados separadamente em cada Ordem de Serviço.
- O valor total da OS é calculado automaticamente a partir das peças e dos serviços.
- A mão de obra é controlada separadamente dos valores das peças.
- A aplicação registra quem foi o responsável pela execução do serviço.
- As Ordens de Serviço possuem controle de status: Aberta, Em andamento, Finalizada e Cancelada.
- Cada veículo é vinculado a um cliente, permitindo consultar o histórico de Ordens de Serviço.
- As informações de pagamento são registradas na OS.
- O sistema permite editar uma Ordem de Serviço sem perder o vínculo com o cliente e o veículo.
- O valor de repasse da mão de obra é tratado internamente pelo sistema e não é exibido no documento destinado ao cliente.

## Demonstração

A aplicação possui uma interface web desenvolvida para facilitar a rotina da oficina, permitindo o gerenciamento de clientes, veículos, Ordens de Serviço e informações financeiras.

### Nova Ordem de Serviço

Tela para abertura de uma nova Ordem de Serviço, com seleção do cliente e veículo, inclusão de peças e serviços, responsável pela mão de obra e forma de pagamento.

### Ordens de Serviço

Tela de acompanhamento das Ordens de Serviço cadastradas, com consulta, visualização, edição e controle de status.

### Edição de Ordem de Serviço

Permite atualizar informações da OS, incluindo peças, serviços, valores, responsável, forma de pagamento e status.

### Financeiro

Área destinada à consulta e acompanhamento dos valores das Ordens de Serviço, com filtros por período e formas de pagamento.

### Dashboard

Painel com indicadores e gráficos para acompanhamento do faturamento e da operação da oficina.


---

## Arquitetura simplificada

```text
Usuário
   |
   v
Interface Web
HTML + CSS + JavaScript
   |
   v
Flask / Python
   |
   +--------------------+
   |                    |
   v                    v
Regras de negócio     Geração de PDF
   |
   v
SQLite
   |
   +----------------------------+
   |            |               |
Clientes     Veículos        Ordens de Serviço
                                |
                                +---- Peças
                                |
                                +---- Serviços
                                |
                                +---- Pagamentos





