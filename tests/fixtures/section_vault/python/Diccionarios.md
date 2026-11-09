# Diccionarios

Sin metadatos a propósito: esta nota se encuentra solo con la búsqueda léxica.

## Crear diccionarios

```python
edades = {"ana": 31, "luis": 28}
vacio = dict()
```

## Recorrer un diccionario

Para recorrer claves y valores a la vez se usa `items()`:

```python
for nombre, edad in edades.items():
    print(nombre, edad)
```

## Claves que no existen

`edades["pepe"]` lanza `KeyError`; `edades.get("pepe", 0)` devuelve un valor por
defecto.
