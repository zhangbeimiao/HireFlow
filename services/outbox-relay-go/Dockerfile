FROM golang:1.27-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/outbox-relay ./cmd/relay

FROM alpine:3.21
RUN addgroup -S hireflow && adduser -S -G hireflow hireflow
COPY --from=build /out/outbox-relay /usr/local/bin/outbox-relay
USER hireflow
EXPOSE 9101
ENTRYPOINT ["outbox-relay"]
