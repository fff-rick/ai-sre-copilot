FROM golang:1.26.7-bookworm AS builder

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/tool-gateway ./cmd/server

FROM alpine:3.22
RUN addgroup -S app && adduser -S -G app app
COPY --from=builder /out/tool-gateway /usr/local/bin/tool-gateway
USER app
EXPOSE 8081 9091
ENTRYPOINT ["/usr/local/bin/tool-gateway"]
