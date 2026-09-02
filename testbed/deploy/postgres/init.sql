CREATE TABLE IF NOT EXISTS inventory (
    sku text PRIMARY KEY,
    available integer NOT NULL CHECK (available >= 0)
);

INSERT INTO inventory (sku, available)
VALUES
    ('widget-blue', 1000),
    ('widget-red', 1000)
ON CONFLICT (sku) DO UPDATE SET available = EXCLUDED.available;

