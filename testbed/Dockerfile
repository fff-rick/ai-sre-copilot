FROM golang:1.26-bookworm AS builder

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/testbed-service ./cmd/service

FROM alpine:3.23
RUN addgroup -S -g 10001 app && adduser -S -D -H -u 10001 -G app app
COPY --from=builder /out/testbed-service /usr/local/bin/testbed-service
USER app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/testbed-service"]

