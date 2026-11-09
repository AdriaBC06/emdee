# Listas

## Crear listas

`numeros = [3, 1, 2]` o `list(range(5))`.

## Ordenar una lista

`sorted(numeros)` devuelve una lista nueva; `numeros.sort(reverse=True)` la
ordena en el sitio de mayor a menor.

## Comprensiones de listas

```python
cuadrados = [n * n for n in numeros if n % 2 == 0]
```
