import gradio as gr
from query import describe_index, safe_query


gr.set_static_paths(
    paths=[
        "/app/app"
    ]
)


css = """
footer {
    display: none !important;
}


/* Common icon button style */
#search_btn,
#clear_btn,
#copy_btn {

    width: 60px !important;
    height: 60px !important;
    min-width: 60px !important;

    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;

    padding: 0 !important;

    background-size: 45px 45px !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
}


/* Button images */
#search_btn {
    background-image: url('/gradio_api/file=/app/app/search.png') !important;
}

#clear_btn {
    background-image: url('/gradio_api/file=/app/app/clear.png') !important;
}

#copy_btn {
    background-image: url('/gradio_api/file=/app/app/copy.png') !important;
}


/* Hide default text */
#search_btn span,
#clear_btn span,
#copy_btn span {
    display: none !important;
}


/* Hover */
#search_btn:hover,
#clear_btn:hover,
#copy_btn:hover {
    opacity: 0.7;
    transform: scale(1.05);
}


/* Click animation */
#search_btn:active,
#clear_btn:active,
#copy_btn:active {
    transform: scale(0.9);
}


/* Answer area */
#answer_box {
    /* ChatGPT-like expanding answer area: do not clip generated text. */
    height: auto !important;
    min-height: 180px;
    max-height: none !important;
    overflow: visible !important;
    overflow-wrap: anywhere;
    white-space: normal;

    border: 1px solid #444;
    padding: 12px;
    line-height: 1.6;
}


/* Status */
.status_box {

    color: #888;
    font-style: italic;

}


/* Index info line */
.index_info {
    color: #888;
    font-size: 0.9em;
}
"""


# System fonts only: the default theme pulls fonts from Google, which is not
# reachable on the offline PC.
theme = gr.themes.Default(
    font=["ui-sans-serif", "system-ui", "sans-serif"],
    font_mono=["ui-monospace", "Consolas", "monospace"],
)


def chat(query):
    return safe_query(query)



def on_click(q):

    return (
        "🔎 Searching papers and generating answer...",
        ""
    )



def clear_all():

    return (
        "",
        "",
        ""
    )



with gr.Blocks(analytics_enabled=False) as ui:


    gr.Markdown(
        "# 📚 Local Scopus AI (Offline)"
    )


    index_info = gr.Markdown(
        "",
        elem_classes=["index_info"]
    )


    inp = gr.Textbox(
        lines=4,
        placeholder="Ask an academic question..."
    )


    status = gr.Markdown(
        "",
        elem_classes=["status_box"]
    )


    out = gr.Markdown(
        "",
        elem_id="answer_box"
    )


    with gr.Row(equal_height=False):


        btn = gr.Button(
            "",
            elem_id="search_btn",
            variant="secondary",
            scale=0
        )


        clear_btn = gr.Button(
            "",
            elem_id="clear_btn",
            variant="secondary",
            scale=0
        )


        copy_btn = gr.Button(
            "",
            elem_id="copy_btn",
            variant="secondary",
            scale=0
        )



    # Search pipeline

    btn.click(
        fn=on_click,
        inputs=inp,
        outputs=[status, out],
        queue=False

    ).then(
        fn=chat,
        inputs=inp,
        outputs=out

    ).then(
        fn=lambda: "",
        outputs=status

    ).then(
        fn=describe_index,
        outputs=index_info
    )


    # Show which index is live (it changes after an ingest)

    ui.load(
        fn=describe_index,
        outputs=index_info,
        queue=False
    )



    # Clear

    clear_btn.click(
        fn=clear_all,
        inputs=None,
        outputs=[
            inp,
            status,
            out
        ],
        queue=False
    )



    # Copy answer

    copy_btn.click(
        None,
        None,
        None,
        js="""
        () => {
            const box = document.querySelector('#answer_box');
            if (!box) {
                alert("❌ Answer box not found");
                return;
            }




            const text = box.innerText;

            if (!text) {

                alert("⚠️ No answer to copy");
                return;

            }



            navigator.clipboard.writeText(text)

            .then(() => {

                alert("✅ Answer copied to clipboard!");

            })

            .catch(err => {

                console.error(err);

                alert("❌ Copy failed");

            });

        }
        """
    )



if __name__ == "__main__":

    ui.launch(
        server_name="0.0.0.0",
        server_port=7860,
        css=css,
        theme=theme
    )

    