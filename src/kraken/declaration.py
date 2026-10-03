"""What this version declares beside its role (W8.1, contract v11).

No secret settings: Kraken's public market data needs no key. The storage
it asks for holds its raw responses (decisions/028): a `dgm` is an edge role.
Its catalogue (convert.CATALOGUE) is not declared here, because v11 has
nowhere to declare one; v14's `DatasetDeclaration` in the declaration is
where it goes.
"""

from meridian.declaration import Declaration, Storage

#: How long a raw response is kept: the reach of a re-conversion.
RETENTION_DAYS = 400

DECLARATION = Declaration(storage=Storage(retention_days=RETENTION_DAYS))
