// Package config loads one testbed service role from environment variables.
package config

import "os"

// Config contains process-local endpoints. Secrets are injected, never defaulted for production use.
type Config struct {
	ServiceName    string
	ServiceVersion string
	ListenAddress  string
	OrderURL       string
	InventoryURL   string
	PaymentURL     string
	DatabaseURL    string
	LogPath        string
}

// Load returns safe local defaults used only by the isolated testbed.
func Load() Config {
	return Config{
		ServiceName:    value("SERVICE_NAME", "api"),
		ServiceVersion: value("SERVICE_VERSION", "1.0.0"),
		ListenAddress:  value("LISTEN_ADDRESS", ":8080"),
		OrderURL:       value("ORDER_URL", "http://order:8080"),
		InventoryURL:   value("INVENTORY_URL", "http://inventory:8080"),
		PaymentURL:     value("PAYMENT_URL", "http://payment:8080"),
		DatabaseURL:    os.Getenv("DATABASE_URL"),
		LogPath:        os.Getenv("TESTBED_LOG_PATH"),
	}
}

func value(name, fallback string) string {
	if current := os.Getenv(name); current != "" {
		return current
	}
	return fallback
}
