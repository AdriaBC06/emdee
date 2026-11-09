# Guía: metadatos de búsqueda en las notas (emdee)

> Pega este fichero tal cual en la sesión que escribe las notas. Explica cómo
> anotar cada apartado para que la **búsqueda avanzada** de emdee (Ctrl+E, o
> `emdee vault search`) encuentre el apartado exacto al escribir unas palabras,
> una pregunta, o al pegar código, un error o el enunciado. Todo funciona sin
> internet y sin IA: la búsqueda solo ve lo que está escrito en la nota.

## Qué hay que hacer

Debajo de **cada heading** (`#`, `##`, `###`…) que explique algo buscable, pon un
comentario HTML `<!-- busca … -->`. No se ve en la vista previa. Ejemplo:

```markdown
## Construcción de DataFrames
<!-- busca
claves: crear dataframe, construir tabla, nuevo DataFrame, create dataframe
aka: crear un DataFrame, DataFrame desde diccionario
para: crear un DataFrame a partir de un diccionario de listas, de una lista de filas o de un array
preguntas: ¿cómo creo un dataframe?; ¿cómo paso un diccionario a una tabla?
api: pd.DataFrame, pd.DataFrame.from_dict, pd.DataFrame.from_records, columns=, index=
errores: ValueError: All arrays must be of the same length
-->

Texto del apartado…
```

## Formato exacto

- El comentario empieza por `<!-- busca` y termina en `-->`, cada uno en su
  propia línea, **justo debajo del heading** (antes del texto).
- Dentro va **un campo por línea**: `campo: valor, valor, valor`. Si una línea
  no empieza por `campo:`, continúa el campo anterior.
- Separa los valores con `,` (o con `;`). En `preguntas` y `para` usa `;`
  porque son frases con comas. Las comas dentro de paréntesis no separan:
  `pd.merge(left, right, on="id")` es un solo valor.
- Todos los campos son opcionales; pon solo los que aporten.
- Los nombres en inglés también valen (`keywords`, `aka`, `intent`,
  `questions`, `api`, `errors`).

| Campo | Para qué | Peso en el ranking |
|---|---|---|
| `aka` | Otros **títulos** que podría tener el apartado: cómo lo llamaría alguien que no ha leído la nota. | Casi como el título |
| `claves` | Palabras clave y **sinónimos ES + EN** de lo que se explica. | Alto |
| `api` | Funciones, métodos, clases, parámetros (`pd.read_csv`, `.groupby`, `how=`), comandos. | Alto (muy alto si se pega código) |
| `errores` | Excepciones y mensajes de error que este apartado ayuda a resolver, tal como salen. | Alto (muy alto si se pega un error) |
| `para` | Para qué sirve / qué problema resuelve, en una frase. | Medio |
| `preguntas` | Preguntas típicas de examen que este apartado responde. | Medio |

El título del heading, los headings que lo contienen y el título de la nota
puntúan solos: no hace falta repetirlos en `claves`.

## Reglas para escribir buenos metadatos

1. **Cada apartado responde a algo concreto.** Si un heading mezcla tres
   cosas, mejor tres `###` con su propio comentario.
2. **`claves`: entre 4 y 10.** Piensa en lo que *escribiría* alguien buscando
   esto: verbos de acción (crear, filtrar, unir, ordenar, quitar, rellenar…) +
   el objeto (dataframe, columna, fila, nulos…). Incluye al menos un sinónimo en
   español y su equivalente en inglés (`unir tablas`, `join`, `merge`).
3. **`aka`: 1–3 títulos alternativos**, frases cortas.
4. **`api`: todos los nombres de código que aparecen o se deberían usar**,
   escritos como en el código (`df.groupby`, `pd.concat`, `ascending=False`).
   Con el prefijo habitual (`pd.`, `np.`, `plt.`) y también el método suelto si
   se llama sobre una variable (`.agg`). Los parámetros, acabados en `=`.
5. **`errores`: copia el nombre de la excepción y la parte fija del mensaje**
   (`KeyError`, `ValueError: The truth value of a Series is ambiguous`). Nada de
   rutas ni números de línea.
6. **`para`: una frase** con el verbo y el resultado ("ordenar filas por una
   columna, de mayor a menor").
7. **`preguntas`: 1–3 preguntas** como las formularía un enunciado.
8. **No metas el temario entero en `claves`.** Palabras que no tienen que ver
   con el apartado lo hacen salir en búsquedas ajenas.
9. **El cuerpo también cuenta:** que el texto use las palabras normales del
   tema y que los ejemplos de código sean completos y ejecutables (se buscan
   cuando se pega código).
10. **Opcional, por nota:** en el front-matter, `claves:` y `api:` valen para
    todos los apartados de la nota (con menos peso):
    ```yaml
    ---
    tags: [pandas, tema-2]
    claves: pandas, tablas de datos
    ---
    ```

## Ejemplo completo de nota bien anotada

````markdown
---
tags: [pandas, tema-3]
---

# Agrupar y agregar

Resumir una tabla por grupos de filas. Antes de esto: [[DataFrames]].

## groupby básico
<!-- busca
claves: agrupar, agrupación, group by, resumen por grupo, agregar por categoría
aka: groupby, calcular por grupos
para: calcular un resumen (media, suma, conteo) para cada grupo de filas
preguntas: ¿cómo calculo la media de ventas por ciudad?; ¿cómo agrupo por una columna?
api: df.groupby, .mean(), .sum(), .count(), .size(), as_index=
-->

`groupby` separa las filas por los valores de una columna y aplica una
función a cada grupo:

```python
df.groupby("ciudad")["ventas"].mean()
df.groupby("ciudad", as_index=False)["ventas"].sum()
```

## Ordenar resultados
<!-- busca
claves: ordenar, sort, ranking, top, de mayor a menor
para: ordenar filas por una o varias columnas, ascendente o descendente
preguntas: ¿cómo ordeno de mayor a menor?; ¿cómo saco los 5 mayores?
api: df.sort_values, ascending=False, df.sort_index, df.nlargest
errores: KeyError (la columna por la que ordenas no existe)
-->

```python
df.sort_values("ventas", ascending=False)
df.nlargest(5, "ventas")
```
````

## Ejemplo de mala anotación (y por qué)

```markdown
## Varios
<!-- claves: pandas, python, datos, tabla, código, ejemplo, ejercicio, examen -->
Cosas de groupby, merge y gráficos…
```

- Heading vago ("Varios") que mezcla tres temas → ninguno sale el primero.
- `claves` genéricas que están en todas las notas → no distinguen nada.
- Sin `api` ni `errores` → al pegar `df.merge(...)` o un traceback no gana.
- Falta la línea de apertura `busca` (funciona igual si hay algún `campo:`
  conocido, pero escríbela siempre).

Corregido: tres apartados (`## groupby`, `## merge`, `## Gráficos de barras`),
cada uno con sus claves concretas, su `api` y sus errores típicos.

## Comprobar que funciona

```bash
emdee vault search ~/apuntes "crear un dataframe" --explain   # sección, puntuación y campos que coincidieron
pbpaste | emdee vault search ~/apuntes --stdin               # pegar código o un traceback (xclip -o en Linux)
emdee vault search ~/apuntes "unir dos tablas" --json
```

Si una búsqueda importante no saca el apartado esperado entre los primeros,
añade a ese apartado las palabras que usaste (en `claves`, `aka` o `preguntas`).
Las notas sin comentario siguen encontrándose por su texto.
