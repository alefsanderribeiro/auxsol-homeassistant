# Auxsol Solar — Home Assistant

Integração não-oficial para monitorar usinas solares Auxsol Cloud
(`auxsolcloud.com`) no Home Assistant.

Criada por engenharia reversa da API do portal, porque a Auxsol não publica
documentação e o datalogger ESP32 não expõe acesso local.

## O que ela faz

- Login automático com renovação de token (12 h) — a senha é guardada na config
  entry do HA, nunca em texto no YAML
- Sensores de potência, geração (dia / mês / ano / total), rede (tensão, corrente,
  frequência), temperatura do inversor, sinal do datalogger e economia em BRL
- Suporte a múltiplas usinas na mesma conta (seletor no config flow)
- Intervalo de atualização configurável (60–3600 s)
- Correção do total acumulado: a API inclui energia gerada antes do cadastro da
  usina no portal, o que infla o total. O componente desconta esse baseline.

## Instalação via HACS

[![Abrir no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alefsanderribeiro&repository=auxsol-homeassistant&category=integration)

1. HACS → Integrações → ⋮ → Repositórios personalizados
2. Adicione `https://github.com/alefsanderribeiro/auxsol-homeassistant`
   com categoria **Integration**
3. Instale e reinicie o Home Assistant
4. Configurações → Dispositivos e serviços → Adicionar integração → **Auxsol Solar**

## Instalação manual

```bash
cd /config/custom_components
git clone https://github.com/alefsanderribeiro/auxsol-homeassistant.git _tmp
cp -r _tmp/custom_components/auxsol .
rm -rf _tmp
```

Reinicie o Home Assistant.

## Configuração

Credenciais do portal [auxsolcloud.com](https://www.auxsolcloud.com) — as mesmas
do site e do app. Depois de validar, o fluxo lista as usinas da conta para você
escolher qual monitorar.

O intervalo de atualização é ajustável em *Configurações → Dispositivos e serviços
→ Auxsol Solar → Configurar*.

## Sobre a frequência dos dados

O datalogger publica na nuvem **a cada 5 minutos** — é o limite do equipamento.
O componente pode consultar a API a cada 60 s, mas o valor só muda quando o
datalogger envia. Não existe telemetria em tempo real nesse hardware.

## Limitações conhecidas

- Requer internet; não há acesso local ao datalogger
- A API é privada e não documentada — pode mudar sem aviso
- Contas marcadas como *visitante* na usina não têm acesso ao histórico detalhado
  (curva de geração); apenas leitura em tempo real e totais
- Total acumulado depende do baseline de comissionamento (ver `BASELINE_KWH` em
  `const.py`); se a usina mudar, ajuste esse valor

## Isenção de responsabilidade

Projeto não-oficial, sem vínculo com a Auxsol. Use por sua conta e risco. Este
software apenas **lê** dados; ele não altera configuração de inversor nem de
datalogger.

## Licença

MIT
