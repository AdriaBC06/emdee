---
title: DataFrames en pandas
tags: [pandas, tema-2]
claves: pandas, tabla de datos
---

# DataFrames en pandas

Un DataFrame es una tabla bidimensional con etiquetas en filas y columnas.
Antes de esto: [[Listas]] y [[Diccionarios]].

## Construcción de DataFrames
<!-- busca
claves: crear dataframe, construir tabla, nuevo DataFrame, create dataframe
aka: crear un DataFrame, DataFrame desde diccionario, DataFrame desde listas
para: crear un DataFrame a partir de un diccionario de listas, de una lista de filas o de un array de NumPy
preguntas: ¿cómo creo un dataframe?; ¿cómo paso un diccionario a una tabla?; ¿cómo pongo nombres a las columnas?
api: pd.DataFrame, pd.DataFrame.from_dict, pd.DataFrame.from_records, columns=, index=
errores: ValueError: All arrays must be of the same length
-->

La forma más habitual es pasar un diccionario cuyas claves son los nombres de
las columnas y cuyos valores son listas de la misma longitud:

```python
import pandas as pd

df = pd.DataFrame({
    "ciudad": ["Madrid", "Lugo", "Vigo"],
    "ventas": [120, 80, 95],
})
```

También desde una lista de filas, indicando las columnas con `columns=`:

```python
df = pd.DataFrame([["Madrid", 120], ["Lugo", 80]], columns=["ciudad", "ventas"])
```

Si las listas tienen longitudes distintas pandas lanza
`ValueError: All arrays must be of the same length`.

## Selección de columnas y filas
<!-- busca
claves: seleccionar columna, acceder a fila, indexar, select column
aka: loc e iloc, acceder a datos
para: obtener una o varias columnas, o filas por etiqueta (loc) o por posición (iloc)
preguntas: ¿diferencia entre loc e iloc?; ¿cómo saco una columna?
api: df["col"], df[["a", "b"]], df.loc, df.iloc, df.at
errores: KeyError: 'col' (la columna no existe), IndexError: single positional indexer is out-of-bounds
-->

`df["ventas"]` devuelve una Serie; `df[["ciudad", "ventas"]]` un DataFrame.

```python
df.loc[0, "ciudad"]      # por etiqueta
df.iloc[0, 1]            # por posición
```

Pedir una columna que no existe produce `KeyError: 'edad'`: revisa
`df.columns` (mayúsculas, espacios).

## Filtrar filas por condición
<!-- busca
claves: filtrar filas, condición, máscara booleana, filter rows, where
para: quedarse solo con las filas que cumplen una condición
preguntas: ¿cómo filtro las filas donde ventas > 100?; ¿cómo combino condiciones?
api: df[df["col"] > x], df.query, &, |, ~, isin, between
errores: ValueError: The truth value of a Series is ambiguous
-->

```python
df[df["ventas"] > 100]
df[(df["ventas"] > 80) & (df["ciudad"] != "Lugo")]
df.query("ventas > 100")
```

Usa `&` y `|` con paréntesis; `and`/`or` dan
`ValueError: The truth value of a Series is ambiguous`.

## Valores nulos
<!-- busca
claves: valores nulos, NaN, datos faltantes, missing values, rellenar huecos
aka: tratar NaN, limpiar nulos
para: detectar, eliminar o rellenar valores que faltan
preguntas: ¿cómo quito las filas con NaN?; ¿cómo relleno los nulos con la media?
api: df.isna, df.isnull, df.dropna, df.fillna, df.notna
-->

```python
df.isna().sum()          # nulos por columna
df.dropna()              # quita filas con algún NaN
df.fillna(df["ventas"].mean())
```
