SAMPLE DOCUMENT (fictional): Ferrostack v2.4 Technical Specification

About this sample
Ferrostack is a fictional inventory system invented for this demo.

Overview
Ferrostack is a warehouse inventory service that tracks stock levels across multiple locations. Version 2.4 replaces the polling-based sync with an event-driven design and adds batch reservations. The service exposes a REST API and publishes domain events to a message bus.

Data model
The core tables are items, locations, stock_levels, and reservations. An item has a SKU, a name, and optional attributes stored as JSON. A stock level row links one item to one location with a quantity and a version number used for optimistic concurrency. Reservations hold stock for an order for 15 minutes; unconfirmed reservations expire automatically through a sweeper job that runs every 60 seconds.

Sync protocol
Every mutation emits an event: stock.increased, stock.decreased, reservation.created, reservation.expired. Events carry a monotonically increasing sequence number per location. Consumers checkpoint the last processed sequence number and replay from it after a restart. Events older than 72 hours are archived to object storage and removed from the bus.

API summary
GET /items/{sku} returns the item with current stock per location. POST /reservations creates a reservation and returns 201 with the reservation id, or 409 when stock is insufficient. POST /reservations/{id}/confirm converts a reservation into a stock decrease. DELETE /reservations/{id} releases held stock. All write endpoints require an Idempotency-Key header; retried requests with the same key return the original response.

Consistency model
Reads are eventually consistent and may lag writes by up to 2 seconds under normal load. The reservation path is strongly consistent: it checks and decrements available stock in a single transaction using the version number, so two concurrent reservations cannot oversell the last unit. If the service restarts mid-request, the idempotency store guarantees the mutation happens at most once.

Limits and quotas
The public API allows 600 requests per minute per API key. Batch endpoints accept up to 500 line items per call. Payloads larger than 1 MB are rejected with 413. Rate limit headers (X-RateLimit-Remaining, X-RateLimit-Reset) are returned on every response.
