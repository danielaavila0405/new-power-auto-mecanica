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

