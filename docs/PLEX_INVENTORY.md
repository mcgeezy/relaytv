# Plex Route-Surface Inventory

This machine-checked inventory preserves the Plex architecture boundary. The
routes package owns HTTP registration, request bounds, response headers, and
error mapping. `integrations/plex_auth.py`, `plex_client.py`, and
`plex_service.py` own account lifecycle, transport, and product behavior and
must never import the routes package.

The listing includes every function defined in `app/relaytv_app/routes/` whose
name contains `plex` (case-insensitive). `tests/test_plex_inventory.py` pins
the exact route function set and verifies the integration modules do not
import routes.

Regenerate the listing after an intentional route change with:

    PYTHONPATH=app python3 tests/test_plex_inventory.py --write

## Plex Function Listing

<!-- BEGIN GENERATED PLEX ROUTE LISTING (tests/test_plex_inventory.py) -->
### `routes/plex.py` (17)

- `plex_artwork`
- `plex_auth_cancel`
- `plex_auth_poll`
- `plex_auth_start`
- `plex_disconnect`
- `plex_home`
- `plex_integration_status`
- `plex_integration_test`
- `plex_item_action`
- `plex_item_children`
- `plex_item_detail`
- `plex_libraries`
- `plex_library_items`
- `plex_search`
- `plex_server_select`
- `plex_servers`
- `plex_stream`
<!-- END GENERATED PLEX ROUTE LISTING -->
