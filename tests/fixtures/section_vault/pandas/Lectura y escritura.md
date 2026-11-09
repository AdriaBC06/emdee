# Lectura y escritura de ficheros

## Leer CSV con read_csv
<!-- busca
claves: leer csv, cargar fichero, importar datos, read csv, separador, codificación
aka: abrir un CSV
para: cargar un fichero CSV en un DataFrame eligiendo separador, codificación y cabecera
preguntas: ¿cómo leo un csv separado por punto y coma?; ¿qué hago si da error de codificación?
api: pd.read_csv, sep=, encoding=, header=, usecols=, index_col=, decimal=
errores: UnicodeDecodeError: 'utf-8' codec can't decode byte, FileNotFoundError, ParserError: Error tokenizing data
-->

```python
df = pd.read_csv("datos.csv", sep=";", encoding="latin-1", decimal=",")
```

Si aparece `UnicodeDecodeError`, prueba `encoding="latin-1"` o `"cp1252"`.
`FileNotFoundError` indica una ruta mal escrita.

## Guardar a CSV
<!-- busca
claves: guardar csv, exportar, escribir fichero, save csv
para: escribir un DataFrame en un CSV sin la columna del índice
preguntas: ¿cómo guardo sin el índice?
api: df.to_csv, index=False, sep=
-->

```python
df.to_csv("salida.csv", index=False)
```

## Excel

`pd.read_excel("notas.xlsx", sheet_name="Hoja1")` necesita `openpyxl`.
