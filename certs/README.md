# Сертификаты для проверки TLS roskazna.gov.ru

Сертификат сайта `roskazna.gov.ru` выпущен удостоверяющим центром Минцифры России
(«Russian Trusted Sub CA»). Ни корневого, ни промежуточного сертификата этого УЦ нет
в `certifi`, хранилище macOS и большинстве дистрибутивов Linux. Кроме того, сервер не
передаёт промежуточный сертификат в цепочке. Поэтому `curl` и `requests` по умолчанию
завершаются ошибкой `unable to get local issuer certificate`.

Проверку TLS мы **не отключаем**. Вместо этого `src/roskazna_kbs/http.py` собирает
бандл `certifi + *.pem из этого каталога` (`data/.cache/ca-bundle.pem`).

| Файл | Субъект | Откуда | SHA-256 отпечаток | Действует до |
|---|---|---|---|---|
| `russian_trusted_root_ca.pem` | Russian Trusted Root CA | https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt | `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` | 2032-02-27 |
| `russian_trusted_sub_ca_2024.pem` | Russian Trusted Sub CA (2024) | http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt (поле AIA сертификата сайта) | `21:55:78:50:36:C9:00:DB:B5:F1:BB:2A:15:69:C8:0C:55:59:5B:D6:BF:94:86:7A:29:BB:DD:BC:7D:88:A3:F2` | 2029-07-19 |

Проверка цепочки:

```bash
echo | openssl s_client -connect roskazna.gov.ru:443 -servername roskazna.gov.ru 2>/dev/null | openssl x509 > /tmp/rk.pem
cat certs/russian_trusted_root_ca.pem certs/russian_trusted_sub_ca_2024.pem > /tmp/rk-bundle.pem
openssl verify -CAfile /tmp/rk-bundle.pem /tmp/rk.pem   # → OK
```

Если сайт перейдёт на новый промежуточный сертификат, возьмите его по адресу из поля
`Authority Information Access → CA Issuers` сертификата сайта и положите сюда в PEM.
Сертификат «Russian Trusted Sub CA» 2022 года (с gu-st.ru) к текущему сертификату сайта
не подходит — у него другой ключ.
