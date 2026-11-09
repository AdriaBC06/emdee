# Combinar tablas

## merge
<!-- busca
claves: unir tablas, combinar dataframes, cruzar tablas, join SQL
aka: merge, unir dos DataFrames por una columna
para: unir dos tablas por una o varias columnas comunes, como un JOIN de SQL
preguntas: ¿cómo uno dos tablas por una columna?; ¿qué hace how="left"?
api: pd.merge, df.merge, on=, how=, left_on=, right_on=, suffixes=
errores: MergeError, KeyError (la columna de unión no existe)
-->

```python
pd.merge(ventas, clientes, on="id_cliente", how="left")
```

`how` puede ser `"inner"` (por defecto), `"left"`, `"right"` u `"outer"`.

## concat
<!-- busca
claves: concatenar, apilar tablas, juntar filas, append
aka: concat
para: poner una tabla debajo de otra (filas) o al lado (columnas)
api: pd.concat, axis=, ignore_index=
-->

```python
pd.concat([enero, febrero], ignore_index=True)
pd.concat([a, b], axis=1)
```

## join por índice

`df.join(otro)` une por el índice; es un atajo de `merge` con
`left_index=True, right_index=True`.
