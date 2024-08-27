def arrange_variable_hierarchy(data):
    result = []
    stack = []

    for item in data:
        current_node = {
            "column_id": item[2],
            "column_title": item[3],
            "indent": item[4],
            "children": []
        }

        while stack and stack[-1]["indent"] >= item[4]:
            stack.pop()

        if stack:
            stack[-1]["node"]["children"].append(current_node)

        else:
            result.append(current_node)

        stack.append({"indent": item[4], "node": current_node})

    return result
