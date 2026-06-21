# template-mapping-fields v1: `store_sku_current_position` (draft for confirmation)

Tomorrow's deliverable. All three sections stay; this adds the `store_sku_current_position` fields plus the `store_code` non-mapping object.

## Object shape (full keys, one example)

```json
{
  "key": "sku_id",
  "display_name": "SKU",
  "section": "identity",
  "mandatory": true,
  "constraints": null,
  "datatype": "text",
  "description": "The product identifier as your system reports it.",
  "allowed_values": null,
  "max_length": 128,
  "sink": "core.store_sku_current_position"
}
```

The five property keys above (`mandatory`, `constraints`, `datatype`, `allowed_values`, `max_length`) are shown with illustrative values copied from the existing `sale_event.sku_id`. They are NOT final. CC must re-derive them from the live `core.store_sku_current_position` definition. The `store_sku` column may differ from the `sale_event` projection (different nullability, length, constraint), so nothing is copied across.

## Split of work

- Authored here, review these: `key`, `display_name`, `section`, `description`, `sink`.
- CC derives from the live `core.store_sku_current_position` schema: `datatype`, `mandatory`, `constraints`, `max_length`, `allowed_values`.
- Mapping rule: `mandatory` = column is NOT NULL; `constraints` = the column's constraint (`unique`, etc.) or `null`; `datatype` / `max_length` / `allowed_values` = the column's type, length, and any enum/check vocabulary.

## Open decision (one)

I used JSON `null` (not the string `"null"`) for empty `constraints` and `sink`, to stay consistent with `allowed_values` and `max_length`, which are already JSON `null` in the live response. Mixing the string `"null"` with real `null` in one payload is a parsing footgun on the frontend. Confirm JSON `null`, or say switch to string `"null"`.

## Fields (authored keys only; array order = listing order)

### section: identity

```json
{ "key": "sku_id",        "display_name": "SKU",          "section": "identity", "description": "The product identifier as your system reports it.",        "sink": "core.store_sku_current_position" }
{ "key": "sku_variant",   "display_name": "SKU variant",  "section": "identity", "description": "Variant qualifier (size, colour) when your SKUs carry one.", "sink": "core.store_sku_current_position" }
{ "key": "sku_lot_batch", "display_name": "Lot / batch",  "section": "identity", "description": "Lot or batch number when tracked.",                       "sink": "core.store_sku_current_position" }
{ "key": "barcode",       "display_name": "Barcode",      "section": "identity", "description": "Scannable barcode (EAN, UPC, ...) for the item, if present.", "sink": "core.store_sku_current_position" }
```

### section: product

```json
{ "key": "product_name",         "display_name": "Product name",        "section": "product", "description": "Human-readable product name.",                                  "sink": "core.store_sku_current_position" }
{ "key": "product_description",  "display_name": "Product description", "section": "product", "description": "Longer descriptive text for the product, if provided.",          "sink": "core.store_sku_current_position" }
{ "key": "product_category",     "display_name": "Category",            "section": "product", "description": "Top-level category your system files the product under.",        "sink": "core.store_sku_current_position" }
{ "key": "product_sub_category", "display_name": "Sub-category",        "section": "product", "description": "Sub-category beneath the main category.",                       "sink": "core.store_sku_current_position" }
{ "key": "product_department",   "display_name": "Department",          "section": "product", "description": "Store department or division the product belongs to.",          "sink": "core.store_sku_current_position" }
{ "key": "supplier_id",          "display_name": "Supplier ID",         "section": "product", "description": "Your identifier for the product's supplier.",                   "sink": "core.store_sku_current_position" }
{ "key": "packaging_type",       "display_name": "Packaging type",      "section": "product", "description": "How the unit is packaged (each, case, pack, ...).",             "sink": "core.store_sku_current_position" }
{ "key": "sku_size",             "display_name": "Size",                "section": "product", "description": "Declared size of the unit (e.g. 500ml, 1kg).",                  "sink": "core.store_sku_current_position" }
{ "key": "unit_of_measure",      "display_name": "Unit of measure",     "section": "product", "description": "Unit the quantity is counted in (each, kg, litre, ...).",       "sink": "core.store_sku_current_position" }
```

### section: pricing

```json
{ "key": "current_retail_price", "display_name": "Retail price",   "section": "pricing", "description": "Current listed shelf price per unit.",                            "sink": "core.store_sku_current_position" }
{ "key": "unit_cost",            "display_name": "Unit cost",      "section": "pricing", "description": "Your cost per unit, when the source reports it.",                 "sink": "core.store_sku_current_position" }
{ "key": "promo_price",          "display_name": "Promo price",    "section": "pricing", "description": "Promotional price per unit while a promotion is active.",         "sink": "core.store_sku_current_position" }
{ "key": "promo_identifier",     "display_name": "Promotion ID",   "section": "pricing", "description": "Identifier of the promotion applied, if any.",                    "sink": "core.store_sku_current_position" }
{ "key": "tax_treatment",        "display_name": "Tax treatment",  "section": "pricing", "description": "How the item is taxed (standard, exempt, ...) per your system.",  "sink": "core.store_sku_current_position" }
{ "key": "currency",             "display_name": "Currency",       "section": "pricing", "description": "ISO 4217 code (e.g. EUR). If absent in the file, provide it as a constant derive.", "sink": "core.store_sku_current_position" }
```

### section: inventory

```json
{ "key": "stock_qty",      "display_name": "Stock quantity",  "section": "inventory", "description": "Units currently on hand.",                       "sink": "core.store_sku_current_position" }
{ "key": "reorder_point",  "display_name": "Reorder point",   "section": "inventory", "description": "Stock level at which the item is reordered.",     "sink": "core.store_sku_current_position" }
{ "key": "lead_time_days", "display_name": "Lead time (days)","section": "inventory", "description": "Days between reorder and replenishment.",         "sink": "core.store_sku_current_position" }
{ "key": "receipt_date",   "display_name": "Receipt date",    "section": "inventory", "description": "Date the current stock was received.",            "sink": "core.store_sku_current_position" }
```

### section: expiry

```json
{ "key": "expiry_date",       "display_name": "Expiry date",       "section": "expiry", "description": "Expiry or best-before date for the current stock.",        "sink": "core.store_sku_current_position" }
{ "key": "expiry_source",     "display_name": "Expiry source",     "section": "expiry", "description": "Where the expiry date came from (label, system, estimate).", "sink": "core.store_sku_current_position" }
{ "key": "expiry_confidence", "display_name": "Expiry confidence", "section": "expiry", "description": "How reliable the expiry value is, as your system rates it.",  "sink": "core.store_sku_current_position" }
```

### section: regulatory_status

```json
{ "key": "regulatory_flag", "display_name": "Regulatory flag", "section": "regulatory_status", "description": "Whether the item is subject to regulatory handling.", "sink": "core.store_sku_current_position" }
{ "key": "regulatory_type", "display_name": "Regulatory type", "section": "regulatory_status", "description": "The kind of regulatory handling that applies.",       "sink": "core.store_sku_current_position" }
{ "key": "sku_status",      "display_name": "SKU status",      "section": "regulatory_status", "description": "Lifecycle state of the SKU (active, discontinued, ...).", "sink": "core.store_sku_current_position" }
```

## store_code (non-mapping object)

No canonical column, so `sink` is `null`. It has no live column to derive from, so `datatype`, `mandatory`, `constraints`, `max_length`, `allowed_values` are declared product decisions (my leans below), not schema-derived. Confirm or adjust.

```json
{
  "key": "store_code",
  "display_name": "Store code",
  "section": "store",
  "mandatory": false,
  "constraints": null,
  "datatype": "text",
  "description": "Your store identifier. Used to route the row to the correct store; it is not stored as a canonical SKU field.",
  "allowed_values": null,
  "max_length": null,
  "sink": null
}
```

Confirmed for `store_code`: `section` = `store`; `mandatory` = false; `datatype` = text.

## Ignore sentinel (one object, not one per column)

A single reserved target. Any source column the tenant does not want imported is mapped to this one object; many source columns can select it. This is not an enumeration of unmapped columns.

```json
{
  "key": "__ignore__",
  "display_name": "Ignore (do not import)",
  "section": "system",
  "mandatory": false,
  "constraints": null,
  "datatype": null,
  "description": "Assign any source column you do not want imported. More than one column can map here; all are dropped.",
  "allowed_values": null,
  "max_length": null,
  "sink": null
}
```

## How the consumer reads each object

- `sink` set: maps to that canonical column.
- `sink` null and `key` is `__ignore__`: drop the source column.
- `sink` null and any other key (e.g. `store_code`): functional, used by the pipeline but not stored.

No `role` key. `key` plus `sink` carry intent.

## Knock-on (not tomorrow's scope)

The shape change adds `sink` and `constraints` to every object. The existing `sale_event` and `change_event` objects need the same two keys added when those sections are produced. Flagging so it is not forgotten; out of scope for the store_sku deliverable.
