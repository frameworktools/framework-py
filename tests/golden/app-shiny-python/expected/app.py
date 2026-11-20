import numpy as np
import pandas as pd
from shiny import App, render, ui

app_ui = ui.page_sidebar(
    ui.sidebar(ui.input_slider("n", "Rows", min=5, max=100, value=20)),
    ui.card(ui.card_header("A random sample"), ui.output_data_frame("table")),
    title="Demo App",
)


def server(input, output, session):
    @render.data_frame
    def table():
        rng = np.random.default_rng(1)
        return pd.DataFrame({"value": rng.normal(size=input.n())})


app = App(app_ui, server)
