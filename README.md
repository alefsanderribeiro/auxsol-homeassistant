# Auxsol Solar — Home Assistant

Integração **não-oficial** para monitorar usinas da plataforma **Auxsol Cloud**
(`auxsolcloud.com`) no Home Assistant.

Criada por engenharia reversa da API do portal: a Auxsol não publica documentação
pública e os dataloggers ESP32 não expõem acesso local (só conexão de saída).

> **Projeto independente.** Sem vínculo com a AUXSOL / Grupo AUX. Use por sua conta
> e risco. Este software apenas **lê** dados — não altera configuração de inversor,
> bateria ou datalogger.

---

## Instalação

### Via HACS (recomendado)

[![Abrir no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alefsanderribeiro&repository=auxsol-homeassistant&category=integration)

1. **HACS → Integrações → ⋮ → Repositórios personalizados**
2. Adicione `https://github.com/alefsanderribeiro/auxsol-homeassistant`
   com categoria **Integration**
3. Instale e reinicie o Home Assistant
4. **Configurações → Dispositivos e serviços → Adicionar integração → Auxsol Solar**

### Manual

```bash
cd /config
git clone https://github.com/alefsanderribeiro/auxsol-homeassistant.git _auxsol
cp -r _auxsol/custom_components/auxsol custom_components/
rm -rf _auxsol
```

Reinicie o Home Assistant.

### Requisitos

- Home Assistant **2024.6** ou superior
- Conta na Auxsol Cloud (a mesma do site e do app)
- Conexão com a internet — não há modo local

---

## Configuração

Informe o e-mail e a senha do portal. Depois de validar, o fluxo lista as usinas
da conta para você escolher qual monitorar. Contas com várias usinas podem
adicionar a integração mais de uma vez, uma por usina.

### Opções

**Configurações → Dispositivos e serviços → Auxsol Solar → Configurar**

| Opção | Padrão | Descrição |
|---|---|---|
| Intervalo de atualização | 60 s | Frequência de consulta à API (60–3600 s) |
| Energia de comissionamento | *detectado* | kWh gerados antes do cadastro da usina. Deixe vazio para detecção automática |

---

## Sensores

### Inversor (sempre presente)

| Entidade | Unidade |
|---|---|
| Potência atual | kW |
| Geração hoje / no mês / no ano / total | kWh |
| Horas de plena carga | h |
| Capacidade instalada | kW |
| Potência na rede | kW |
| Tensão / corrente / frequência da rede | V / A / Hz |
| Temperatura do inversor | °C |
| Sinal do datalogger | % |
| Economia hoje / no mês / total | BRL |
| Última leitura | timestamp |
| Status | texto |

### Bateria

Estado de carga (SOC), saúde (SOH), tensão, corrente, potência, temperatura,
ciclos e capacidade disponível.

### Medidor

Energia importada e exportada, potência ativa / reativa / aparente, fator de
potência, frequência, tensão e corrente por fase (A, B, C).

### Estação meteorológica

Temperatura ambiente, máxima, mínima, ponto de orvalho, temperatura do painel,
umidade, pressão, radiação solar (instantânea e diária), velocidade e direção do
vento e duração da insolação.

### Sensor PID

Corrente de fuga, tensão de saída e temperaturas da placa de controle, do
conversor Boost e do Flyback.

> Sensores aparecem **automaticamente** conforme os equipamentos são detectados
> na sua conta. Uma usina só com inversor mostrará apenas os sensores do inversor.

---

## Sobre o total de geração

O portal expõe dois totais: `yt` (acumulado do inversor) e `ym` (do mês). Quando o
inversor já vinha gerando **antes** de a usina ser cadastrada — o que acontece
durante o comissionamento —, `yt` inclui essa energia, mas a série diária não.
Resultado: o total fica maior que a soma dos dias.

A integração isola esse excedente automaticamente:

```
comissionamento = yt − ym − Σ(meses anteriores da série anual)
```

O cálculo roda a cada 6 horas. Se você preferir informar o valor manualmente,
preencha **Energia de comissionamento** nas opções — o campo tem prioridade sobre
a detecção.

> Para usinas criadas no mesmo mês da primeira geração, nem sempre há excedente e
> o valor detectado será 0. Isso é esperado.

---

## Cobertura de equipamentos

A integração lê a **plataforma em nuvem**, não o modelo do equipamento. Por isso
qualquer inversor cadastrado na Auxsol Cloud é atendido, incluindo as linhas:

- **On-grid monofásico** — ASN-3SL, ASN-3.3SL, ASN-3.6SL-G2 … ASN-10SL (3–10 kW)
- **On-grid trifásico** — ASN-5TL … ASN-25TL, ASN-30TL-G2 … ASN-40TL (5–40 kW)
- **On-grid trifásico LV** — ASN-10TL-LV … ASN-25TL-LV (10–25 kW)
- **Híbridos monofásicos** — ASG-3.6SL-ZH … ASG-6SL-ZH (3,6–6 kW)
- **Híbridos trifásicos** — ASG-5TL-ZH … ASG-20TL-ZH (5–20 kW)
- **C&I** — ASN-50TL-G2 … ASN-110TL, ASN-90TL-PLUS, ASN-300/320/330TL-HV (50–330 kW)

Equipamentos auxiliares reconhecidos pela API:

| Tipo | Status |
|---|---|
| Bateria (ABL-T HV, ABL01 LV) | suportado |
| Medidor (N34G12 e similares) | suportado |
| Estação meteorológica | suportado |
| Sensor PID | suportado |

**Não suportado** (não existe endpoint na API do portal):

- Carregadores de veículo elétrico (7/11/22 kW, OCPP 1.6J)
- HEMS
- Bombas de calor R290
- Armazenamento em container (EESC, EESB-L)

Esses produtos são gerenciados por outros sistemas da AUXSOL e não aparecem na
conta do portal. Se a Auxsol publicar endpoints para eles, dá para adicionar.

---

## Frequência dos dados

O datalogger publica na nuvem **a cada 5 minutos** — limite do equipamento. A
integração pode consultar a API a cada 60 s, mas o valor só muda quando o
datalogger envia. **Não existe telemetria em tempo real** nesse hardware.

O app oficial da Auxsol usa o mesmo endpoint REST em intervalo curto; não há
canal WebSocket.

---

## Solução de problemas

**Login recusado**
Use a mesma conta do site. Se você entra como convidado (*guest*) em uma usina,
o histórico detalhado fica limitado.

**Sensores de bateria/medidor não aparecem**
A usina não tem esse equipamento cadastrado, ou ele não está vinculado ao mesmo
datalogger do inversor.

**Total diferente da soma dos dias**
Ajuste **Energia de comissionamento** nas opções.

**Entidades ficaram `unavailable` após atualizar**
Alguma versão intermediária alterou `unique_id`. Remova a integração e adicione
novamente — o histórico do Recorder é preservado.

---

## Privacidade

- Credenciais ficam apenas na config entry do Home Assistant, no seu servidor
- A integração não envia dados para nenhum servidor além da própria Auxsol
- Nenhum dado de usina é coletado pelo autor

---

## Licença

MIT — veja [LICENSE](LICENSE).
