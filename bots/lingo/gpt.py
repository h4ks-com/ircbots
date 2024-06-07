from typing import List

import requests

headers = {"accept": "application/json", "Content-Type": "application/json"}


params = {"provider": "DuckDuckGo"}

message_template = {
    "role": "",
    "content": "",
}


class GPT:
    def __init__(self, contextualize=True) -> None:
        self.context: List = []
        self.contextualize = contextualize

    def completion2message(self, completion: dict) -> dict:
        return {"role": "assistant", "content": completion["completion"]}

    def send_message(self, message: str):
        self.json_data = {"messages": []}
        self.user_message = message_template
        self.user_message["role"] = "user"
        self.user_message["content"] = message
        if self.contextualize:
            self.json_data["messages"].extend(self.context)
        self.json_data["messages"].append(self.user_message)
        self.request = requests.post(
            "https://g4f.cloud.mattf.one/api/completions",
            params=params,
            headers=headers,
            json=self.json_data,
        )
        self.completion = self.request.json()
        if self.contextualize:
            self.response = self.completion2message(self.completion)
            self.context.append(self.response)
        return self.completion


if __name__ == "__main__":
    gpt = GPT()
    while True:
        a = input("send>")
        print(gpt.send_message(a))
