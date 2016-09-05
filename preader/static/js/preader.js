$(function() {
    $.getJSON(feed_list_url).done(function(data){
        $.each(data, function(i, item){
            $('<li><a title="' + item.fields.title + '" id="feedLink_' + item.pk + '" href="/f/' + item.pk + '">' + item.fields.title + '</a></li>').appendTo('ul#feedList');
        });
    }).fail(function(){console.log('error')});
});
function get_id(ele, del='_', num=1){
    return ele.attr('id').split(del)[num];
}