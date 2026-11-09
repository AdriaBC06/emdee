---
tags: [pandas, tema-3]
---

# Agrupar y agregar

## groupby básico
<!-- busca
claves: agrupar, agrupación, group by, agregar por grupo
aka: groupby
para: calcular un resumen (media, suma, conteo) para cada grupo de filas
preguntas: ¿cómo calculo la media de ventas por ciudad?; ¿cómo agrupo por una columna?
api: df.groupby, .mean(), .sum(), .count(), .size(), as_index=
-->

```python
df.groupby("ciudad")["ventas"].mean()
df.groupby("ciudad", as_index=False)["ventas"].sum()
```

El resultado tiene como índice los valores de la columna de agrupación.

## Varias funciones con agg
<!-- busca
claves: agregación múltiple, varias estadísticas, aggregate
aka: agg
para: aplicar varias funciones de agregación a la vez, o una distinta por columna
api: .agg, .aggregate, pd.NamedAgg
-->

```python
df.groupby("ciudad")["ventas"].agg(["mean", "sum", "max"])
df.groupby("ciudad").agg(total=("ventas", "sum"), media=("ventas", "mean"))
```

## Ordenar resultados
<!-- busca
claves: ordenar, sort, ranking, top
para: ordenar filas por una o varias columnas, de mayor a menor o de menor a mayor
preguntas: ¿cómo ordeno de mayor a menor?; ¿cómo saco los 5 mayores?
api: df.sort_values, ascending=False, df.sort_index, df.nlargest, df.nsmallest
-->

```python
df.sort_values("ventas", ascending=False)
df.nlargest(5, "ventas")
```
