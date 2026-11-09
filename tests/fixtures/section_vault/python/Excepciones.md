# Excepciones

## Capturar errores con try
<!-- busca
claves: capturar excepción, manejar errores, try except, catch
para: evitar que el programa se pare cuando algo falla
api: try, except, else, finally, except ValueError as e
-->

```python
try:
    numero = int(texto)
except ValueError as e:
    print("No es un número:", e)
```

## Lanzar excepciones con raise
<!-- busca
claves: lanzar excepción, provocar error, raise, throw
aka: raise
para: señalar un error desde tu propia función
api: raise ValueError("..."), raise from
-->

```python
def dividir(a, b):
    if b == 0:
        raise ValueError("b no puede ser 0")
    return a / b
```
