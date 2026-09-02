// Package model defines the small HTTP contract shared by testbed roles.
package model

// CheckoutRequest travels through the API and order services.
type CheckoutRequest struct {
	SKU         string `json:"sku"`
	Quantity    int    `json:"quantity"`
	AmountCents int    `json:"amount_cents"`
}

// CheckoutResponse is returned after inventory and payment both succeed.
type CheckoutResponse struct {
	OrderID string `json:"order_id"`
	Status  string `json:"status"`
}

// InventoryResponse reports the remaining stock after a reservation.
type InventoryResponse struct {
	SKU       string `json:"sku"`
	Remaining int    `json:"remaining"`
}

// PaymentResponse reports the simulated charge result.
type PaymentResponse struct {
	Status string `json:"status"`
}
