#!/bin/sh
set -eu

umask 077

rm -f /postgres/* /rabbitmq/* /client/*

openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
  -days 2 -subj "/CN=HireFlow TLS Test CA" \
  -keyout /client/ca.key -out /client/ca.crt

issue_certificate() {
  name="$1"
  destination="$2"
  usage="$3"

  openssl req -newkey rsa:2048 -sha256 -nodes \
    -subj "/CN=${name}" \
    -keyout "${destination}/server.key" \
    -out "${destination}/server.csr"
  cat > "${destination}/server.ext" <<EOF
subjectAltName=DNS:${name},DNS:localhost,IP:127.0.0.1
extendedKeyUsage=${usage}
keyUsage=digitalSignature,keyEncipherment
EOF
  openssl x509 -req -sha256 -days 2 \
    -in "${destination}/server.csr" \
    -CA /client/ca.crt -CAkey /client/ca.key -CAcreateserial \
    -extfile "${destination}/server.ext" \
    -out "${destination}/server.crt"
  cp /client/ca.crt "${destination}/ca.crt"
  rm -f "${destination}/server.csr" "${destination}/server.ext"
}

issue_certificate postgres-tls /postgres serverAuth
issue_certificate rabbitmq-tls /rabbitmq serverAuth

openssl req -newkey rsa:2048 -sha256 -nodes \
  -subj "/CN=hireflow-tls-client" \
  -keyout /client/client.key -out /client/client.csr
cat > /client/client.ext <<EOF
extendedKeyUsage=clientAuth
keyUsage=digitalSignature,keyEncipherment
EOF
openssl x509 -req -sha256 -days 2 \
  -in /client/client.csr \
  -CA /client/ca.crt -CAkey /client/ca.key -CAcreateserial \
  -extfile /client/client.ext \
  -out /client/client.crt
rm -f /client/client.csr /client/client.ext /client/ca.srl

chown -R 70:70 /postgres
chown -R 100:101 /rabbitmq
chmod 600 /postgres/server.key /rabbitmq/server.key /client/client.key /client/ca.key
chmod 644 /postgres/server.crt /postgres/ca.crt \
  /rabbitmq/server.crt /rabbitmq/ca.crt \
  /client/client.crt /client/ca.crt

