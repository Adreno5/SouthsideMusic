from pprint import pprint
from colorama import Fore, Style

import ollama

models = [model['name'] for model in ollama.list()['models']]
print('models:')
for i, model in enumerate(models):
    print(f'  {i}. {model}')
idx = int(input('model index > '))
model = models[idx]
print(f'using model {model}')

messages: list[ollama.Message] = []

while True:
    print('\n')
    messages.append({'role': 'user', 'content': input('prompt > ')})

    gene = ollama.chat(model, messages, stream=True)
    thinking = True
    print(Fore.LIGHTBLACK_EX)

    for chunk in gene:
        message: ollama.Message = chunk['message'] # type: ignore
        if message['content'] and thinking:
            print(f'{Style.RESET_ALL}\n')
            print(message['content'], end='', flush=True)
            thinking = False
        elif message.get('thinking') and thinking: # type: ignore
            print(message['thinking'], end='', flush=True) # type: ignore
        else:
            print(message['content'], end='', flush=True)